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
import random
import sys
import threading
from datetime import datetime, timezone

import typer

from . import economy
from .captcha import TURNSTILE_PAGE_URL, TURNSTILE_SITEKEY, build_solver
from .client import ConsoAPIError, ConsoClient
from .config import Settings
from .identity import build_identity
from .pipeline import farm_turns_for_account, run_registration
from .session import SessionManager
from .storage import Store
from .transport import Transport
from .verifiers import build_verifier

app = typer.Typer(add_completion=False, help="Conso AI Usage Tracker automation pipeline.")

_print_lock = threading.Lock()


def _log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    with _print_lock:
        typer.echo(f"{stamp} | {message}")


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
    dry_run: bool = typer.Option(False, "--dry-run", help="Generate identities only, no network."),
) -> None:
    """Provision accounts."""
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
    _log(f"register start: count={count} concurrency<={settings.max_concurrency} "
         f"verifier={type(verifier).__name__} solver={type(solver).__name__}")
    results = run_registration(
        settings, count, referral_code=referral, store=store,
        verifier=verifier, solver=solver, logger=_log,
    )
    ok = sum(1 for r in results if r.status == "active")
    _log(f"register done: {ok}/{len(results)} active -> {store.json_path}")


@app.command()
def farm(
    turns: int = typer.Option(10, help="Turns per account."),
    email: str = typer.Option("", help="Farm a single account by email."),
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
    for record in records:
        ok, zaps = farm_turns_for_account(settings, record, turns, solver=solver, store=store, logger=_log)
        total_ok += ok
        total_zaps += zaps
        store.update(record.email, total_zaps=zaps, status="farmed" if ok else record.status)
    _log(f"farm done: {total_ok} turns accepted, ~{round(total_zaps, 2)} zaps")


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


def main() -> None:
    try:
        app()
    except ConsoAPIError as exc:
        typer.echo(f"error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
