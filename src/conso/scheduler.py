"""
Daily earning loop.

Runs the full earn cycle for stored accounts on a schedule:

  every cycle (default daily):
    - ensure session (refresh; no Turnstile unless the chain broke)
    - claim missions (check-in, tweet, article) — idempotent per day
    - farm turns up to the daily cap, stopping at the first zero-credit turn
    - record the run in a local ledger

The loop is resumable: it persists last-run state per account and skips
accounts that already completed today's cycle, so a restart or overlap does not
double-submit or trip the anti-abuse cap.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from .client import ConsoAPIError, ConsoClient
from .config import Settings
from .earnings import run_earnings
from .limits import get_daily_status
from .pipeline import farm_turns_for_account
from .session import SessionManager
from .storage import AccountRecord, Store

LogFn = Callable[[str], None]


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _log(logger: LogFn | None, message: str) -> None:
    if logger:
        logger(message)


@dataclass
class LoopConfig:
    turns: int = 10                     # turns per account per day (<= DAILY_TURN_CAP)
    interval_seconds: int = 24 * 3600   # between cycles
    claim_missions: bool = True
    max_cycles: int = 0                 # 0 = run forever
    daily_cap: float = 0.0              # 0 = no cap


@dataclass
class CycleResult:
    day: str
    per_account: dict[str, dict] = field(default_factory=dict)

    @property
    def zaps(self) -> float:
        return sum(v.get("zaps", 0.0) for v in self.per_account.values())


class DailyLoop:
    """Schedule and run the daily earn cycle, resumable across restarts."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        config: LoopConfig | None = None,
        solver=None,
        verifier=None,
        logger: LogFn | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.config = config or LoopConfig()
        self.solver = solver
        self.logger = logger
        self.verifier = verifier
        self.manager = SessionManager(settings, store, verifier=verifier)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    # -- ledger ------------------------------------------------------------
    def _ledger(self) -> dict:
        return self.store.load_state().get("daily_loop", {})

    def _mark_done(self, day: str, email: str, info: dict) -> None:
        state = self.store.load_state()
        loop = state.setdefault("daily_loop", {})
        loop.setdefault(day, {})[email] = info
        # keep only the last 14 days
        for old in sorted(loop)[:-14]:
            loop.pop(old, None)
        state["daily_loop"] = loop
        self.store.save_state(state)

    def _done_today(self, day: str, email: str) -> bool:
        return email in self._ledger().get(day, {})

    # -- one account -------------------------------------------------------
    def run_account(self, record: AccountRecord, *, force: bool = False) -> dict:
        day = _today_utc()
        if not force and self._done_today(day, record.email):
            return {"status": "skipped", "reason": "already ran today"}

        client = ConsoClient(self.settings)
        try:
            result = self.manager.ensure_session(record, solver=self.solver)
            client.session = result.session
        except ConsoAPIError as exc:
            client.close()
            return {"status": "failed", "reason": f"session: {exc}"}

        info: dict = {"status": "ok", "session": result.source, "zaps": 0.0}
        try:
            status = get_daily_status(client)
            if status and status.is_banned:
                return {"status": "banned", "reason": "account_banned"}
            if self.config.daily_cap and status:
                if status.effective_daily >= self.config.daily_cap:
                    return {"status": "capped", "reason": f"daily={status.effective_daily}"}

            before = status.total_zaps if status else 0.0

            if self.config.claim_missions:
                summary = run_earnings(client, logger=self.logger)
                info["missions"] = summary.zaps_earned
                before = summary.total_zaps_after or before
        finally:
            client.close()

        ok, farmed = farm_turns_for_account(
            self.settings, record, self.config.turns,
            solver=self.solver, store=self.store, verifier=self.verifier, logger=self.logger,
        )
        info["turns"] = ok
        info["zaps"] = round(info.get("zaps", 0.0) + farmed, 2)
        self._mark_done(day, record.email, info)
        return info

    # -- full cycle --------------------------------------------------------
    def run_cycle(self, *, force: bool = False) -> CycleResult:
        day = _today_utc()
        result = CycleResult(day=day)
        for record in self.store.all():
            if self._stop.is_set():
                break
            if record.status not in ("active", "farmed"):
                continue
            try:
                info = self.run_account(record, force=force)
            except Exception as exc:  # noqa: BLE001
                info = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:160]}
            result.per_account[record.email] = info
            _log(self.logger, f"loop: {record.email} -> {info.get('status')} "
                              f"{info.get('reason', '')} +{info.get('zaps', 0)} zaps")
        return result

    def run_forever(self) -> None:
        cycles = 0
        while not self._stop.is_set():
            if self.config.max_cycles and cycles >= self.config.max_cycles:
                break
            _log(self.logger, f"loop: cycle {cycles + 1} start")
            result = self.run_cycle()
            _log(self.logger, f"loop: cycle {cycles + 1} done, +{round(result.zaps, 2)} zaps")
            cycles += 1
            if self.config.max_cycles and cycles >= self.config.max_cycles:
                break
            # interruptible sleep
            end = time.monotonic() + self.config.interval_seconds
            while time.monotonic() < end and not self._stop.is_set():
                time.sleep(min(5, end - time.monotonic()))
