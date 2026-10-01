"""
Provisioning + turn-farming pipeline.

Two flows:

  register()  : create an account (Supabase auth) -> create the Conso user
                record -> optionally set consoname / redeem referral.

  farm_turns(): log in -> submit synthetic AI-usage turns via append_prompt,
                respecting the client-side zap accounting and pacing.

Both are driven through ConsoClient and persist through Store, with adaptive
concurrency and resumable checkpoints.
"""

from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import economy
from . import constants as C
from .captcha import CaptchaSolver
from .client import ConsoAPIError, ConsoClient, Session
from .concurrency import AdaptiveConcurrency, Pacer
from .config import Settings
from .identity import build_identity
from .session import SessionManager, jwt_expiry
from .storage import AccountRecord, Store
from .transport import Transport
from .verifiers import EmailVerifier

LogFn = Callable[[str], None]


@dataclass
class PipelineResult:
    email: str
    status: str
    note: str = ""


def _log(default: LogFn | None, message: str) -> None:
    if default:
        default(message)


def _wait_for_code(verifier: EmailVerifier, address: str, *, timeout: float) -> str | None:
    """Fetch a confirmation code/link from the verifier (wait_for_otp or link)."""
    otp_fn = getattr(verifier, "wait_for_otp", None)
    if callable(otp_fn):
        result = otp_fn(address, timeout=timeout)
        return str(result) if result else None
    result = verifier.wait_for_link(address, timeout=timeout)
    return str(result) if result else None


def register_account(
    settings: Settings,
    *,
    index: int = 0,
    verifier: EmailVerifier | None = None,
    solver: CaptchaSolver | None = None,
    referral_code: str = "",
    logger: LogFn | None = None,
) -> AccountRecord:
    """Provision a single account end-to-end."""
    rng = random.Random()
    try:
        identity = build_identity(settings, rng=rng, index=index)
    except ValueError:
        # No EMAIL_DOMAIN configured; the verifier must provision its own inbox.
        from .identity import Identity, generate_consoname, generate_password

        identity = Identity(
            email="", password=generate_password(rng, settings.password_length),
            display_name="", consoname=generate_consoname(rng),
        )

    # If the verifier can provision its own inbox (mail.tm), use that address
    # instead of the EMAIL_DOMAIN-derived one.
    create_inbox = getattr(verifier, "create_inbox", None)
    if callable(create_inbox):
        try:
            address, password = create_inbox()
            identity.email = address
            if password:
                identity.password = password
        except Exception as exc:  # noqa: BLE001
            _log(logger, f"register: inbox provision failed: {exc}")

    # Supabase signup always requires a password, even for OTP-only inboxes.
    if not identity.password:
        from .identity import generate_password

        identity.password = generate_password(rng, settings.password_length)

    if not identity.email:
        record = AccountRecord.now(email="", status="failed", note="no email domain / inbox")
        return record

    record = AccountRecord.now(email=identity.email, password=identity.password, consoname=identity.consoname)
    client = ConsoClient(settings)
    try:
        # Conso enforces Turnstile on signup + login; solve once and reuse.
        captcha_token: str | None = None
        if solver is not None:
            captcha_token = solver.solve_turnstile()
            _log(logger, f"register: {identity.email} captcha solved")

        _log(logger, f"register: {identity.email} -> supabase signup")
        signup = client.sign_up_email(identity.email, identity.password, captcha_token=captcha_token)

        # Conso confirms via a 6-digit email OTP (not a link). Fetch it from the
        # verifier and exchange it for a session.
        session = None
        if verifier is not None:
            code = _wait_for_code(verifier, identity.email, timeout=120.0)
            if code:
                _log(logger, f"register: {identity.email} got code {code}")
                session = client.verify_otp(identity.email, code)

        # Fallback: a project with auto-confirm lets password login through.
        if session is None:
            session = client.sign_in_password(
                identity.email, identity.password, captcha_token=captcha_token
            )

        record.user_id = session.user_id
        record.access_token = session.access_token
        record.refresh_token = session.refresh_token
        record.expires_at = session.expires_at or jwt_expiry(session.access_token)

        # Create the Conso user row (extension passes the OAuth subject id).
        try:
            client.create_consouser(session.user_id)
        except ConsoAPIError as exc:
            _log(logger, f"register: create_consouser note: {exc}")

        # Redeem the referral BEFORE onboarding: the extension shows the
        # referral screen (redeem referral/access code) right after
        # createConsouser and before picking a consoname.
        if referral_code:
            try:
                client.redeem_referral_code(referral_code)
                _log(logger, f"register: {identity.email} referral={referral_code}")
            except ConsoAPIError as exc:
                _log(logger, f"register: referral note: {exc}")

        # Complete onboarding: set the display name, matching the extension flow
        # (createConsouser -> referral -> pick consoname -> ready) before any turn.
        if identity.consoname:
            try:
                client.set_consoname(identity.consoname)
                _log(logger, f"register: {identity.email} consoname={identity.consoname}")
            except ConsoAPIError as exc:
                _log(logger, f"register: set_consoname note: {exc}")

        record.status = "active"
        record.note = "registered"
        return record
    except ConsoAPIError as exc:
        record.status = "failed"
        record.note = str(exc)[:200]
        return record
    except Exception as exc:  # noqa: BLE001 - surface any transport failure
        record.status = "failed"
        record.note = f"{type(exc).__name__}: {exc}"[:200]
        return record
    finally:
        client.close()


def run_registration(
    settings: Settings,
    count: int,
    *,
    referral_code: str = "",
    store: Store | None = None,
    verifier: EmailVerifier | None = None,
    solver: CaptchaSolver | None = None,
    logger: LogFn | None = None,
) -> list[PipelineResult]:
    store = store or Store(settings.data_dir)
    engine = AdaptiveConcurrency(initial=settings.concurrency, maximum=settings.max_concurrency)
    pacer = Pacer(settings.min_delay_seconds, settings.max_delay_seconds)
    results: list[PipelineResult] = []
    lock = threading.Lock()
    state = store.load_state()
    done = int(state.get("register_done", 0))

    def task(i: int) -> AccountRecord:
        pacer.wait()
        record = register_account(
            settings, index=i, verifier=verifier, solver=solver,
            referral_code=referral_code, logger=logger,
        )
        if record.status == "active":
            engine.report_success()
        else:
            engine.report_error()
        return record

    with ThreadPoolExecutor(max_workers=settings.max_concurrency) as pool:
        futures: dict[Any, int] = {}
        pending = list(range(done, done + count))
        cursor = 0

        def submit_more() -> None:
            nonlocal cursor
            while cursor < len(pending) and len(futures) < engine.limit:
                i = pending[cursor]
                cursor += 1
                futures[pool.submit(task, i)] = i

        submit_more()
        while futures:
            for future in as_completed(list(futures)):
                futures.pop(future, None)
                record = future.result()
                store.add(record)
                with lock:
                    results.append(PipelineResult(record.email, record.status, record.note))
                    done += 1
                _log(logger, f"[{record.status}] {record.email} :: {record.note}")
                submit_more()
                break
            store.save_state({"register_done": done})

    return results


# ---------------------------------------------------------------------------
# Turn farming
# ---------------------------------------------------------------------------
@dataclass
class TurnSpec:
    platform: str
    model: str
    prompt_text: str
    response_text: str
    has_non_image_attachment: bool = False


def make_synthetic_turn(rng: random.Random, index: int) -> TurnSpec:
    """Generate a plausible, high-quality turn payload."""
    platform = rng.choice(["claude", "chatgpt", "perplexity", "gemini"])
    models = {
        "claude": ["claude-opus-4-8", "claude-sonnet-5", "claude-fable-5"],
        "chatgpt": ["gpt-5-6", "gpt-5-5-thinking", "auto"],
        "perplexity": ["pplx_asi_sonnet", "pplx_asi_opus", "pplx_asi"],
        "gemini": ["gemini-3-pro", "gemini-3.6-thinking", "gemini-3-flash"],
    }
    model = rng.choice(models[platform])
    prompt = (
        "Write a production-grade Python module that parses a JSON configuration file, "
        "validates every field against a schema, applies defaults for missing optional "
        "keys, and returns a typed dataclass. Include error handling, type hints, and a "
        "small usage example in a docstring. Format the output as markdown with a code "
        "block and a short table describing each configuration key.\n"
        f"Context id: {index}-{rng.randint(100000, 999999)}"
    )
    response = (
        "Below is a complete implementation.\n\n"
        "```python\n"
        "from dataclasses import dataclass, field\n"
        "def load_config(path: str) -> dict:\n"
        "    import json\n"
        "    with open(path) as fh:\n"
        "        return json.load(fh)\n"
        "```\n\n"
        "| key | type | default |\n|-----|------|---------|\n| name | str | required |\n"
    ) * rng.randint(2, 4)
    return TurnSpec(platform=platform, model=model, prompt_text=prompt, response_text=response,
                    has_non_image_attachment=rng.random() < 0.1)


def farm_turns_for_account(
    settings: Settings,
    record: AccountRecord,
    turns: int,
    *,
    solver: CaptchaSolver | None = None,
    store: Store | None = None,
    verifier: EmailVerifier | None = None,
    logger: LogFn | None = None,
) -> tuple[int, float]:
    """Submit `turns` synthetic turns for one account. Returns (ok_count, zaps).

    Sessions are resolved through SessionManager, which reuses the cached access
    token, refreshes (persisting the rotated refresh token), and only falls back
    to password login when the refresh chain breaks.
    """
    store = store or Store(settings.data_dir)
    manager = SessionManager(settings, store, verifier=verifier)
    client = ConsoClient(settings)
    pacer = Pacer(settings.min_delay_seconds, settings.max_delay_seconds)
    ok = 0
    credited = 0.0

    # Hard clamp to the measured daily cap: past it the server bans, sometimes
    # without first returning a zero-credit turn.
    effective_turns = min(turns, C.DAILY_TURN_CAP)
    if effective_turns < turns:
        _log(logger, f"farm: {record.email} clamping {turns} -> {effective_turns} (daily cap)")
    try:
        try:
            result = manager.ensure_session(record, solver=solver)
        except ConsoAPIError:
            # ensure_session failed -> try full recovery (refresh/password/OTP)
            verifier = getattr(manager, "verifier", None)
            result = manager.recover(record, solver=solver, verifier=verifier)
        client.session = result.session
        _log(logger, f"farm: {record.email} session={result.source}")

        rng = random.Random()
        for i in range(effective_turns):
            pacer.wait()
            spec = make_synthetic_turn(rng, i)
            account = economy.account_turn(
                platform=spec.platform,
                model=spec.model,
                prompt_text=spec.prompt_text,
                response_text=spec.response_text,
                has_non_image_attachment=spec.has_non_image_attachment,
            )
            entry = economy.build_entry(
                account, timestamp=economy.js_isoformat(datetime.now(timezone.utc))
            )
            try:
                result = client.append_prompt(entry, account.zaps, account.spend_usd)
                ok += 1
                turn_credited = float(_extract_credited(result, account.zaps))
                credited += turn_credited
                _log(logger, f"farm: {record.email} {spec.platform}/{spec.model} +{account.zaps} zaps")

                # Soft abuse flag: the server credits 0 instead of banning. This
                # appears at the daily turn cap (~10/account); continuing after it
                # triggers account_banned. Stop immediately.
                if turn_credited == 0:
                    _log(logger, f"farm: {record.email} credited=0 — daily turn cap reached, stopping")
                    break
            except ConsoAPIError as exc:
                _log(logger, f"farm: {record.email} append_prompt rejected: {exc}")
                if exc.status in (429, 403) or "account_banned" in str(exc):
                    break
        return ok, round(credited, 2)
    finally:
        client.close()


def _extract_credited(result: Any, fallback: float) -> float:
    """The append_prompt RPC returns the credited zaps directly (a number).

    Older/alternate shapes return an object; handle both.
    """
    if isinstance(result, bool):  # bool is an int subclass; not a credit
        return fallback
    if isinstance(result, (int, float)):
        return float(result)
    if isinstance(result, dict):
        for key in ("creditedZaps", "credited_zaps", "zaps"):
            if key in result and isinstance(result[key], (int, float)):
                return float(result[key])
    return fallback
