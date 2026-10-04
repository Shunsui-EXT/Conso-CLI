"""
Conso automation CLI.

Subcommands:
  test       Pre-flight: verify transport, backend reachability, auth settings.
  register   Provision N accounts (adaptive concurrency, resumable).
  farm       Submit synthetic AI-usage turns for stored accounts to earn zaps.
  export     Dump the account store to JSON/CSV.
  verify     Health-check the proxy pool.
"""

from __future__ import annotations

import json
import os
import random
import socket
import sys
import threading
import time
from datetime import datetime, timezone

import typer

from . import economy
from . import ui
from .captcha import TURNSTILE_PAGE_URL, TURNSTILE_SITEKEY, build_solver, ensure_solver_ready
from .monitor import get_monitor, start_monitor, stop_monitor
from .notify import is_configured, notify as tg_notify, summary as tg_summary
from .client import ConsoAPIError, ConsoClient
from .config import Settings
from .earnings import MISSIONS, run_earnings
from .identity import build_identity
from .limits import get_daily_status
from .pipeline import farm_turns_for_account, run_registration
from .scheduler import DailyLoop, LoopConfig
from .session import SessionManager
from .storage import Store
from .transport import Transport
from .verifiers import build_verifier

app = typer.Typer(add_completion=False, help="Conso AI Usage Tracker automation pipeline.")

_print_lock = threading.Lock()


def _log(message: str) -> None:
    with _print_lock:
        ui.log(message, level=ui.classify(message))


def _mirror(logger):
    """Wrap a logger so lines also land in the live monitor's log tail."""
    def _fn(message: str) -> None:
        logger(message)
        mon = get_monitor()
        if mon is not None:
            mon.log(message, level=ui.classify(message))
    return _fn


@app.command()
def test(
    platform: str = typer.Option("claude", help="Platform to simulate."),
    model: str = typer.Option("claude-opus-4-8", help="Model to simulate."),
) -> None:
    """Pre-flight: transport, backend reachability, economy sanity."""
    settings = Settings.from_env()
    _log("pre-flight start")

    # 1) economy sanity on a synthetic turn
    account = economy.account_turn(
        platform=platform,
        model=model,
        prompt_text=(
            "Write a detailed Python function that parses a JSON config file and "
            "validates every key against a schema, returning a typed dataclass.\n"
            "Output format: markdown table and code block."
        ),
        response_text="Here is a complete implementation with tests. " * 12,
    )
    _log(
        f"economy: in={account.inputTokens} out={account.outputTokens} "
        f"q={account.promptQuality} zaps={account.zaps} usd={round(account.spend_usd, 6)}"
    )

    # 2) backend reachability + captcha posture
    transport = Transport(settings)
    try:
        resp = transport.request(
            "GET",
            f"{settings.supabase_url}/auth/v1/settings",
            headers={"apikey": settings.supabase_key},
        )
        data = resp.json()
        external = data.get("external", {})
        _log(
            f"supabase: http={resp.status_code} email={external.get('email')} "
            f"google={external.get('google')} signup_disabled={data.get('disable_signup')} "
            f"autoconfirm={data.get('mailer_autoconfirm')}"
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"supabase unreachable: {exc}")

    # 2b) captcha gate (expected: captcha_failed)
    try:
        resp = transport.request(
            "POST",
            f"{settings.supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": settings.supabase_key, "Content-Type": "application/json"},
            json={"email": "probe@example.invalid", "password": "x"},
        )
        gate = (resp.json() or {}).get("error_code", "none")
        _log(f"auth gate: http={resp.status_code} error_code={gate} (captcha required: {gate == 'captcha_failed'})")
    except Exception as exc:  # noqa: BLE001
        _log(f"auth gate probe failed: {exc}")

    _log(f"turnstile: sitekey={TURNSTILE_SITEKEY} page={TURNSTILE_PAGE_URL}")

    # 3) proxy health
    if settings.proxy.urls:
        results = transport.pool.health_check(settings.impersonate)
        for url, ok in results.items():
            _log(f"proxy {'OK ' if ok else 'DEAD'} {url}")
    else:
        _log("proxy: none configured (direct)")

    transport.close()
    _log("pre-flight done")


@app.command()
def register(
    count: int = typer.Argument(..., help="Number of accounts to create."),
    referral: str = typer.Option("", help="Referral code to redeem per account."),
    earn: bool = typer.Option(False, "--earn", help="Immediately earn zaps after each account registers."),
    turns: int = typer.Option(10, help="Turns per account when --earn is set."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate identities only, no network."),
) -> None:
    """Provision accounts (registration only; use --earn to farm right after)."""
    settings = Settings.from_env()
    if dry_run:
        rng = random.Random()
        verifier = build_verifier(settings)
        provision_inbox = callable(getattr(verifier, "create_inbox", None))
        for i in range(count):
            try:
                identity = build_identity(settings, rng=rng, index=i)
                _log(f"[dry-run] {identity.email} :: {identity.consoname} :: {identity.password}")
            except ValueError:
                if provision_inbox:
                    _log(f"[dry-run] verifier={type(verifier).__name__} will provision inbox at runtime")
                    break
                _log("dry-run: set EMAIL_DOMAIN or use VERIFIER=temptf/mailtm (self-provisioning)")
                break
        return

    store = Store(settings.data_dir)
    verifier = build_verifier(settings)
    solver = build_solver(Transport(settings))
    referral_code = referral or settings.default_referral_code
    ui.banner("register", solver=type(solver).__name__, verifier=type(verifier).__name__,
              proxy=f"{len(settings.proxy.urls)} proxies" if settings.proxy.urls else "direct",
              extra={"count": str(count), "referral": referral_code or "-"})
    ensure_solver_ready(logger=_log)
    start_monitor("register", target=count)
    try:
        results = run_registration(
            settings, count, referral_code=referral_code, store=store,
            verifier=verifier, solver=solver, logger=_mirror(_log),
        )
    finally:
        stop_monitor("DONE")
    ok = sum(1 for r in results if r.status == "active")
    failed = len(results) - ok
    ui.summary("REGISTER DONE", [
        ("active", f"{ok}/{len(results)}"),
        ("failed", failed),
        ("store", store.json_path),
    ], color="green" if ok else "red")
    tg_notify("REGISTER DONE", [
        ("active", f"{ok}/{len(results)}"),
        ("failed", failed),
    ], logger=_log)

    if earn and ok:
        _log(f"register: --earn set, farming {ok} new account(s)")
        start_monitor("register+earn", target=ok)
        try:
            runner = DailyLoop(settings, store, config=LoopConfig(turns=turns),
                               solver=solver, verifier=verifier, logger=_mirror(_log))
            for r in results:
                if r.status != "active":
                    continue
                record = next((a for a in store.all() if a.email == r.email), None)
                if record is None:
                    continue
                info = runner.run_account(record)
                _log(f"register: {r.email} earned +{info.get('zaps', 0)} zaps "
                     f"(status={info.get('status')})")
        finally:
            stop_monitor("DONE")


@app.command()
def farm(
    turns: int = typer.Option(10, help="Turns per account."),
    email: str = typer.Option("", help="Farm a single account by email."),
    earn: bool = typer.Option(True, "--earn/--no-earn", help="Also claim daily/bonus missions."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Compute payloads only, no network."),
) -> None:
    """Submit synthetic AI-usage turns to earn zaps."""
    settings = Settings.from_env()

    if dry_run:
        from .pipeline import make_synthetic_turn

        rng = random.Random()
        for i in range(turns):
            spec = make_synthetic_turn(rng, i)
            account = economy.account_turn(
                platform=spec.platform, model=spec.model,
                prompt_text=spec.prompt_text, response_text=spec.response_text,
                has_non_image_attachment=spec.has_non_image_attachment,
            )
            entry = economy.build_entry(account, timestamp=economy.js_isoformat(datetime.now(timezone.utc)))
            _log(f"[dry-run] {spec.platform}/{spec.model} zaps={account.zaps} entry={json.dumps(entry)}")
        return

    store = Store(settings.data_dir)
    records = store.all()
    if email:
        records = [r for r in records if r.email == email]
    if not records:
        _log("farm: no accounts in store")
        raise typer.Exit(code=1)

    total_ok = 0
    total_zaps = 0.0
    solver = build_solver(Transport(settings))
    verifier = build_verifier(settings)
    manager = SessionManager(settings, store, verifier=verifier)
    for record in records:
        # Claim missions first (cheap zaps), then farm turns.
        if earn:
            client = ConsoClient(settings)
            try:
                client.session = manager.ensure_session(record, solver=solver).session
                summary = run_earnings(client, logger=_log)
                mission_zaps = summary.total_zaps_after - summary.total_zaps_before
                total_zaps += mission_zaps
                _log(f"farm: {record.email} missions +{round(mission_zaps, 2)} zaps")
            except ConsoAPIError as exc:
                _log(f"farm: {record.email} earn skipped: {exc}")
            finally:
                client.close()

        ok, zaps = farm_turns_for_account(
            settings, record, turns, solver=solver, store=store,
            verifier=verifier, logger=_log,
        )
        total_ok += ok
        total_zaps += zaps
        store.update(record.email, total_zaps=zaps, status="farmed" if ok else record.status)
    _log(f"farm done: {total_ok} turns accepted, ~{round(total_zaps, 2)} zaps total")


@app.command()
def export(
    fmt: str = typer.Option("csv", help="csv|json"),
) -> None:
    """Dump the account store."""
    settings = Settings.from_env()
    store = Store(settings.data_dir)
    records = store.all()
    if fmt == "json":
        typer.echo(json.dumps([r.__dict__ for r in records], indent=2, ensure_ascii=False))
    else:
        typer.echo(store.csv_path)
    _log(f"export: {len(records)} accounts -> {store.json_path if fmt == 'json' else store.csv_path}")


@app.command()
def verify() -> None:
    """Health-check the proxy pool."""
    settings = Settings.from_env()
    if not settings.proxy.urls:
        _log("verify: no proxies configured")
        return
    transport = Transport(settings)
    for url, ok in transport.pool.health_check(settings.impersonate).items():
        _log(f"{'OK ' if ok else 'DEAD'} {url}")
    transport.close()


@app.command()
def solve(
    sitekey: str = typer.Option(TURNSTILE_SITEKEY, help="Turnstile sitekey."),
    url: str = typer.Option(TURNSTILE_PAGE_URL, help="Page hosting the widget."),
) -> None:
    """Solve one Turnstile challenge via the configured solver (sanity check)."""
    settings = Settings.from_env()
    transport = Transport(settings)
    solver = build_solver(transport)
    _log(f"solve: provider={type(solver).__name__} sitekey={sitekey}")
    try:
        token = solver.solve_turnstile(sitekey=sitekey, page_url=url)
        _log(f"solved: token_len={len(token)} token={token[:40]}...")
    except Exception as exc:  # noqa: BLE001
        _log(f"solve failed: {exc}")
    finally:
        transport.close()


@app.command()
def session(
    email: str = typer.Option("", help="Account email (empty = all accounts)."),
    force: bool = typer.Option(False, "--force", help="Force a refresh now."),
) -> None:
    """Show/refresh stored sessions without re-authenticating."""
    settings = Settings.from_env()
    store = Store(settings.data_dir)
    manager = SessionManager(settings, store)
    solver = build_solver(Transport(settings))
    records = store.all()
    if email:
        records = [r for r in records if r.email == email]
    if not records:
        _log("session: no accounts")
        return
    for record in records:
        try:
            result = manager.ensure_session(record, solver=solver, force_refresh=force)
            fresh = next((r for r in store.all() if r.email == record.email), record)
            _log(
                f"{record.email} :: source={result.source} "
                f"expires_in={int((fresh.expires_at or 0) - __import__('time').time())}s"
            )
        except ConsoAPIError as exc:
            _log(f"{record.email} :: FAILED {exc}")


@app.command()
def earn(
    email: str = typer.Option("", help="Account email (empty = all accounts)."),
    referral: str = typer.Option("", help="Redeem a referral code."),
    access: str = typer.Option("", help="Redeem an access code."),
    missions: str = typer.Option("", help="Comma-separated mission ids (default: all)."),
    list_only: bool = typer.Option(False, "--list", help="List available missions and exit."),
) -> None:
    """Claim daily/bonus missions and codes to earn zaps."""
    if list_only:
        for mid, meta in MISSIONS.items():
            _log(f"{mid} :: +{meta['reward']} zaps :: {meta['kind']} :: {meta['label']}")
        return

    settings = Settings.from_env()
    store = Store(settings.data_dir)
    manager = SessionManager(settings, store)
    solver = build_solver(Transport(settings))
    records = store.all()
    if email:
        records = [r for r in records if r.email == email]
    if not records:
        _log("earn: no accounts")
        raise typer.Exit(code=1)

    target_missions = [m.strip() for m in missions.split(",") if m.strip()] or None
    for record in records:
        client = ConsoClient(settings)
        try:
            client.session = manager.ensure_session(record, solver=solver).session
        except ConsoAPIError as exc:
            _log(f"earn: {record.email} no session: {exc}")
            continue
        try:
            summary = run_earnings(
                client, missions=target_missions, referral_code=referral,
                access_code=access, logger=_log,
            )
            delta = summary.total_zaps_after - summary.total_zaps_before
            _log(
                f"earn: {record.email} +{round(delta, 2)} zaps "
                f"(total {round(summary.total_zaps_after, 2)}) "
                f"rank {summary.rank_before}->{summary.rank_after}"
            )
            store.update(record.email, total_zaps=summary.total_zaps_after)
        finally:
            client.close()


@app.command()
def status(
    email: str = typer.Option("", help="Account email (empty = all accounts)."),
) -> None:
    """Show daily-limit / earning state for stored accounts."""
    settings = Settings.from_env()
    store = Store(settings.data_dir)
    manager = SessionManager(settings, store)
    solver = build_solver(Transport(settings))
    records = store.all()
    if email:
        records = [r for r in records if r.email == email]
    if not records:
        _log("status: no accounts")
        return
    for record in records:
        client = ConsoClient(settings)
        try:
            client.session = manager.ensure_session(record, solver=solver).session
            st = get_daily_status(client)
            if st is None:
                _log(f"status: {record.email} :: no row")
                continue
            stale = " (stale, resets today)" if st.is_stale else ""
            _log(
                f"status: {record.email} :: total={round(st.total_zaps, 2)} "
                f"daily={round(st.effective_daily, 2)}{stale} "
                f"streak={st.current_streak}/{st.longest_streak} "
                f"boost={st.boost_factor} banned={st.is_banned}"
            )
        except ConsoAPIError as exc:
            _log(f"status: {record.email} :: FAILED {exc}")
        finally:
            client.close()


@app.command()
def loop(
    turns: int = typer.Option(10, help="Turns per account per day (<= daily cap)."),
    interval_hours: float = typer.Option(24.0, help="Hours between cycles."),
    cycles: int = typer.Option(0, help="Number of cycles (0 = forever)."),
    once: bool = typer.Option(False, "--once", help="Run a single cycle now and exit."),
    force: bool = typer.Option(False, "--force", help="Re-run accounts already done today."),
    no_missions: bool = typer.Option(False, "--no-missions", help="Skip mission claims."),
    daily_cap: float = typer.Option(0.0, help="Stop an account at this daily zaps (0 = off)."),
    email: str = typer.Option("", help="Only run this account (empty = all)."),
    workers: int = typer.Option(1, help="Farm this many accounts concurrently (HTTP-only)."),
    min_remaining: float = typer.Option(
        -1.0, "--min-remaining",
        help="Skip farming an account with less than this daily headroom (0 = off, -1 = .env)."),
) -> None:
    """Run the daily earn cycle (missions + turns) on a schedule."""
    import signal

    settings = Settings.from_env()
    store = Store(settings.data_dir)
    solver = build_solver(Transport(settings))
    config = LoopConfig(
        turns=turns,
        interval_seconds=int(interval_hours * 3600),
        claim_missions=not no_missions,
        max_cycles=1 if once else cycles,
        daily_cap=daily_cap,
        parallel_workers=workers,
        min_remaining_zaps=(
            min_remaining if min_remaining >= 0
            else float(os.environ.get("MIN_REMAINING_ZAPS", "1.0") or 0.0)
        ),
    )
    runner = DailyLoop(settings, store, config=config, solver=solver,
                       verifier=build_verifier(settings), logger=_mirror(_log))
    if email:
        runner.only_emails = {email}

    def _handle(signum, frame):  # noqa: ANN001, ARG001
        _log("loop: stop requested, finishing current account...")
        runner.stop()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    ensure_solver_ready(logger=_log)

    start_monitor("loop", target=len(store.all()))
    try:
        if once:
            result = runner.run_cycle(force=force)
        else:
            runner.run_forever()
    finally:
        stop_monitor("DONE")
    if once:
        ui.summary("LOOP DONE", [
            ("zaps", round(result.zaps, 2)),
            ("accounts", len(result.per_account)),
        ], color="green")
        # Ratio against the practical daily budget (21 zaps/account).
        target = 21.0 * max(1, len(result.per_account))
        tg_notify("LOOP DONE", [
            ("zaps", round(result.zaps, 2)),
            ("accounts", len(result.per_account)),
            ("avg / account", round(result.zaps / max(1, len(result.per_account)), 2)),
        ], progress=("daily budget", min(1.0, result.zaps / target)),
           footer=f"host: {socket.gethostname()}", logger=_log)
    else:
        ui.log("loop stopped", level="warn")


@app.command()
def recover(
    email: str = typer.Option("", help="Account email (empty = all accounts)."),
) -> None:
    """Recover a session (refresh -> password -> email OTP) without re-registering."""
    settings = Settings.from_env()
    store = Store(settings.data_dir)
    manager = SessionManager(settings, store)
    solver = build_solver(Transport(settings))
    verifier = build_verifier(settings)
    records = store.all()
    if email:
        records = [r for r in records if r.email == email]
    if not records:
        _log("recover: no accounts")
        raise typer.Exit(code=1)
    for record in records:
        try:
            result = manager.recover(record, solver=solver, verifier=verifier)
            _log(f"recover: {record.email} :: OK source={result.source}")
        except ConsoAPIError as exc:
            _log(f"recover: {record.email} :: FAILED {exc}")


@app.command()
def doctor() -> None:
    """Report whether the pipeline is browser-free and what each layer uses."""
    import importlib.util as _ilu

    settings = Settings.from_env()
    provider = __import__("os").environ.get("CAPTCHA_PROVIDER", "none").strip().lower()
    browser_free_providers = {"capsolver", "2captcha", "manual", "none"}
    browser_libs = ["playwright", "cloakbrowser", "selenium", "pyppeteer", "nodriver"]

    _log("doctor: dependency check")
    for lib in browser_libs:
        present = _ilu.find_spec(lib) is not None
        _log(f"  {lib:14} {'present' if present else 'absent'}")

    # src/ must not import any browser lib
    import pathlib

    src = pathlib.Path(__file__).parent
    hits: list[str] = []
    for py in src.glob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for lib in browser_libs:
            if f"import {lib}" in text or f"from {lib}" in text:
                hits.append(f"{py.name}:{lib}")
    _log(f"  core src/ browser imports: {hits or 'NONE (browser-free)'}")

    _log(f"doctor: CAPTCHA_PROVIDER={provider} "
         f"browser_free={provider in browser_free_providers}")
    if provider == "internal":
        # Check the internal solver is actually usable (installed + browser fetched).
        camoufox_ok = _ilu.find_spec("camoufox") is not None
        browser_ok = False
        if camoufox_ok:
            try:
                from .internal_solver import InternalTurnstileSolver

                browser_ok = InternalTurnstileSolver.is_available()
            except Exception:
                browser_ok = False
        if camoufox_ok and browser_ok:
            _log("  internal solver: camoufox present — ready")
        else:
            _log("  [yellow]internal solver NOT ready[/yellow] — run: "
                 "bash scripts/setup_internal_solver.sh")
    if provider == "service":
        _log("  note: 'service' runs a local CloakBrowser sidecar — NOT browser-free")
        _log("  switch to CAPTCHA_PROVIDER=capsolver (or 2captcha) for full HTTP")
    _log(f"doctor: transport=curl_cffi impersonate={settings.impersonate} "
         f"proxy={'yes' if settings.proxy.urls else 'no'}")


@app.command()
def proxies(
    file: str = typer.Option("", help="Proxy list file (default: SOLVER_PROXY_FILE)."),
    limit: int = typer.Option(0, help="Test only the first N (0 = all)."),
) -> None:
    """Health-check a proxy list (for the solver sidecar)."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    from .captcha import _load_proxies

    settings = Settings.from_env()
    path = file or os.environ.get("SOLVER_PROXY_FILE", "")
    pool = _load_proxies(path)
    if not pool:
        _log(f"proxies: no list at {path!r}")
        raise typer.Exit(code=1)
    if limit:
        pool = pool[:limit]
    transport = Transport(settings)
    _log(f"proxies: testing {len(pool)} from {path}")

    def check(proxy: str) -> tuple[str, bool, str]:
        try:
            resp = transport._session.get(
                "https://api.ipify.org?format=json",
                proxies={"http": proxy, "https": proxy},
                impersonate=settings.impersonate, timeout=12,
            )
            ok = resp.status_code == 200
            return proxy, ok, (resp.text[:40] if ok else f"http {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return proxy, False, str(exc)[:60]

    live = 0
    with ThreadPoolExecutor(max_workers=min(20, len(pool))) as pool_exec:
        for proxy, ok, note in pool_exec.map(check, pool):
            host = proxy.split("@")[-1]
            if ok:
                live += 1
                _log(f"  LIVE {host} {note}")
    _log(f"proxies: {live}/{len(pool)} live")
    transport.close()


@app.command()
def pipeline(
    register: int = typer.Option(0, "--register", help="Accounts to provision (0 = skip)."),
    earn: bool = typer.Option(True, "--earn/--no-earn", help="Earn for new accounts."),
    loop: bool = typer.Option(False, "--loop", help="Run the recurring daily loop after."),
    turns: int = typer.Option(10, help="Turns per account per earn pass."),
    interval_hours: float = typer.Option(24.0, help="Daily-loop interval (hours)."),
    cycles: int = typer.Option(0, help="Daily-loop cycles (0 = forever)."),
    referral: str = typer.Option("", help="Referral code (default from .env)."),
    workers: int = typer.Option(1, help="Farm parallelism (accounts earned concurrently)."),
    daily_cap: float = typer.Option(0.0, help="Stop an account at this daily zaps."),
    only_new: bool = typer.Option(False, "--only-new", help="Earn only the accounts registered this run."),
    solver_concurrent: int = typer.Option(0, help="Solver concurrency (0 = from .env)."),
) -> None:
    """Full pipeline: register -> earn -> daily loop (one entry point)."""
    import signal

    from .orchestrator import Orchestrator, PipelineConfig

    settings = Settings.from_env()
    config = PipelineConfig(
        register_count=register, earn=earn, turns=turns, loop=loop,
        interval_hours=interval_hours, loop_cycles=cycles, referral=referral,
        workers=workers, daily_cap=daily_cap, only_new=only_new,
        solver_concurrent=solver_concurrent,
    )
    orch = Orchestrator(settings, config=config, logger=_mirror(_log))
    ensure_solver_ready(logger=_log)

    def _handle(signum, frame):  # noqa: ANN001, ARG001
        _log("pipeline: stop requested")
        orch.stop()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    start_monitor("pipeline", target=register)
    summary = orch.run()
    stop_monitor("DONE" if not orch._stop.is_set() else "STOPPED")
    ui.summary("PIPELINE DONE", [
        ("registered", summary.get("registered", 0)),
        ("active", summary.get("active", 0)),
        ("earned zaps", round(summary.get("earned_zaps", 0.0), 2)),
    ], color="green")
    _reg = int(summary.get("registered", 0) or 0)
    tg_notify("PIPELINE DONE", [
        ("registered", _reg),
        ("active", summary.get("active", 0)),
        ("earned zaps", round(summary.get("earned_zaps", 0.0), 2)),
    ], progress=("success rate", (summary.get("active", 0) / _reg) if _reg else 0.0),
       footer=f"host: {socket.gethostname()}", logger=_log)


@app.command(name="notify")
def notify_cmd() -> None:
    """Send a test Telegram message (verifies TELEGRAM_BOT_TOKEN/CHAT_ID)."""
    if not is_configured():
        ui.error("Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        raise typer.Exit(1)
    text = tg_summary("CONSO TEST", [("status", "ok"), ("time", time.strftime("%Y-%m-%d %H:%M:%S"))])
    ui.log(f"notify: sending\n{text}")
    tg_notify("CONSO TEST", [("status", "ok"), ("time", time.strftime("%Y-%m-%d %H:%M:%S"))],
              logger=_log)
    ui.log("notify: sent (check your Telegram)", level="ok")


@app.command()
def report(
    fmt: str = typer.Option("table", help="table|json|csv"),
    refresh: bool = typer.Option(False, "--refresh", help="Poll server stats first."),
) -> None:
    """One-shot status report (headless; for cron/logs/piping)."""
    from .earnings import get_account_row
    from .session import SessionManager

    settings = Settings.from_env()
    store = Store(settings.data_dir)
    records = store.all()
    if not records:
        _log("report: no accounts")
        raise typer.Exit(code=1)

    rows: list[dict] = []
    if refresh:
        manager = SessionManager(settings, store)
        for record in records:
            client = ConsoClient(settings)
            try:
                client.session = manager.ensure_session(record).session
                row = get_account_row(client) or {}
                rows.append({
                    "status": "banned" if row.get("is_banned") else record.status,
                    "email": record.email,
                    "total_zaps": float(row.get("total_zaps") or 0),
                    "today": float(row.get("daily_zaps_earned") or 0),
                    "streak": int(row.get("current_streak") or 0),
                    "boost": float(row.get("boost_factor") or 1.0),
                    "proxy": (record.proxy.split("@")[-1] if record.proxy else ""),
                })
            except Exception:  # noqa: BLE001
                rows.append({"status": record.status, "email": record.email,
                             "total_zaps": float(record.total_zaps or 0), "today": 0.0,
                             "streak": 0, "boost": 1.0, "proxy": ""})
            finally:
                client.close()
    else:
        for record in records:
            rows.append({"status": record.status, "email": record.email,
                         "total_zaps": float(record.total_zaps or 0), "today": 0.0,
                         "streak": 0, "boost": 1.0, "proxy": record.proxy or ""})

    active = sum(1 for r in rows if r["status"] == "active")
    banned = sum(1 for r in rows if r["status"] == "banned")
    failed = sum(1 for r in rows if r["status"] == "failed")
    total = sum(r["total_zaps"] for r in rows)

    if fmt == "json":
        typer.echo(json.dumps({
            "summary": {"accounts": len(rows), "active": active, "failed": failed,
                        "banned": banned, "total_zaps": round(total, 2)},
            "accounts": rows,
        }, indent=2, ensure_ascii=False))
    elif fmt == "csv":
        import csv as _csv
        import io

        buf = io.StringIO()
        writer = _csv.DictWriter(buf, fieldnames=["status", "email", "total_zaps",
                                                  "today", "streak", "boost", "proxy"])
        writer.writeheader()
        writer.writerows(rows)
        typer.echo(buf.getvalue().strip())
    else:
        ui.accounts_table(sorted(rows, key=lambda x: x["total_zaps"], reverse=True))
        ui.summary("SUMMARY", [
            ("accounts", len(rows)), ("active", active),
            ("failed", failed), ("banned", banned),
            ("total zaps", f"{total:.1f}"),
        ], color="green" if not failed and not banned else "yellow")


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """No subcommand -> print help (there is no interactive TUI)."""
    if ctx.invoked_subcommand is None:
        ui.banner("help")
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


def main() -> None:
    try:
        app()
    except ConsoAPIError as exc:
        ui.error(str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
