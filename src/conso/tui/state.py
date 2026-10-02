"""Central app state for the Conso TUI (single source of truth)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .events import Event, EventType, get_event_bus


class Sym:
    DONE = "✓"
    RUNNING = "●"
    FAILED = "✗"
    PENDING = "○"


def mask_email(email: str | None) -> str:
    if not email or "@" not in email:
        return "••••••"
    local, domain = email.split("@", 1)
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"


@dataclass
class AccountRow:
    email: str
    status: str = "pending"     # pending | running | active | failed
    stage: str = ""             # create/otp/referral/consoname/earn
    zaps: float = 0.0           # zaps earned this run
    note: str = ""
    proxy: str = ""
    # server-side stats (refreshed on demand)
    total_zaps: float = 0.0
    daily_zaps: float = 0.0
    streak: int = 0
    boost: float = 1.0
    banned: bool = False


@dataclass
class BatchState:
    target: int = 0
    done: int = 0
    failed: int = 0
    status: str = "IDLE"        # IDLE | RUNNING | COMPLETED | STOPPED
    started_at: float = 0.0
    solver: str = ""
    referral: str = ""

    @property
    def elapsed(self) -> float:
        return (time.time() - self.started_at) if self.started_at else 0.0

    @property
    def rate(self) -> float:
        e = self.elapsed
        return (self.done / e * 60.0) if e > 0 and self.done else 0.0


class AppState:
    """Thread-safe state updated from EventBus events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.batch = BatchState()
        self.accounts: dict[str, AccountRow] = {}
        self.logs: list[tuple[float, str]] = []
        self.total_zaps: float = 0.0
        self.turns_ok: int = 0
        self.missions_ok: int = 0
        self._bus = get_event_bus()
        self._bus.subscribe_all(self._on_event)

    # -- event handling ----------------------------------------------------
    def _on_event(self, event: Event) -> None:
        d = event.data
        with self._lock:
            if event.type == EventType.BATCH_STARTED:
                self.batch = BatchState(
                    target=int(d.get("target", 0)),
                    status="RUNNING",
                    started_at=time.time(),
                    solver=d.get("solver", ""),
                    referral=d.get("referral", ""),
                )
            elif event.type == EventType.BATCH_COMPLETED:
                self.batch.status = d.get("status", "COMPLETED")
            elif event.type == EventType.BATCH_STOPPED:
                self.batch.status = "STOPPED"
            elif event.type == EventType.ACCOUNT_QUEUED:
                email = d.get("email", "")
                self.accounts.setdefault(email, AccountRow(email=email)).status = "running"
            elif event.type == EventType.ACCOUNT_STAGE:
                email = d.get("email", "")
                row = self.accounts.setdefault(email, AccountRow(email=email))
                stage = d.get("stage", "")
                row.stage = stage
                # terminal stages must not leave the row stuck on "running"
                if stage in ("skipped", "capped"):
                    row.status = "active"
                    row.note = stage
                else:
                    row.status = "running"
            elif event.type == EventType.ACCOUNT_COMPLETED:
                email = d.get("email", "")
                row = self.accounts.setdefault(email, AccountRow(email=email))
                row.status = "active"
                row.stage = "done"
                row.zaps = float(d.get("zaps", 0) or 0)
                row.proxy = d.get("proxy", "")
                self.batch.done += 1
                self.total_zaps += row.zaps
            elif event.type == EventType.ACCOUNT_FAILED:
                email = d.get("email", "")
                row = self.accounts.setdefault(email, AccountRow(email=email))
                row.status = "failed"
                row.note = d.get("reason", "")[:80]
                self.batch.failed += 1
            elif event.type == EventType.TURN_CREDITED:
                self.turns_ok += 1
                self.total_zaps += float(d.get("zaps", 0) or 0)
            elif event.type == EventType.MISSION_CLAIMED:
                self.missions_ok += 1
                self.total_zaps += float(d.get("zaps", 0) or 0)
            elif event.type == EventType.LOG:
                self.logs.append((event.timestamp, d.get("message", "")))
                if len(self.logs) > 500:
                    self.logs = self.logs[-500:]

    # -- snapshots (copy under lock) --------------------------------------
    def snapshot_accounts(self) -> list[AccountRow]:
        with self._lock:
            return list(self.accounts.values())

    def snapshot_logs(self, limit: int = 100) -> list[tuple[float, str]]:
        with self._lock:
            return self.logs[-limit:]

    def snapshot_batch(self) -> BatchState:
        with self._lock:
            # copy so the UI never mutates the live object
            b = self.batch
            return BatchState(b.target, b.done, b.failed, b.status, b.started_at,
                              b.solver, b.referral)

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            # total = server totals when known, else per-run credits
            server = sum(a.total_zaps for a in self.accounts.values())
            return {
                "zaps": server if server > 0 else self.total_zaps,
                "turns": self.turns_ok,
                "missions": self.missions_ok,
                "accounts": len(self.accounts),
            }

    def load_store(self, store) -> None:
        """Seed the account table from the on-disk store (existing accounts)."""
        with self._lock:
            for rec in store.all():
                if rec.email and rec.email not in self.accounts:
                    self.accounts[rec.email] = AccountRow(
                        email=rec.email,
                        status=rec.status if rec.status in ("active", "failed") else "pending",
                        stage="idle",
                        zaps=float(rec.total_zaps or 0),
                        total_zaps=float(rec.total_zaps or 0),
                        proxy=rec.proxy or "",
                        note=rec.note or "",
                    )
            self.total_zaps = sum(a.total_zaps or a.zaps for a in self.accounts.values())

    def account_counts(self) -> dict[str, int]:
        with self._lock:
            counts = {"active": 0, "failed": 0, "running": 0, "pending": 0}
            for a in self.accounts.values():
                counts[a.status] = counts.get(a.status, 0) + 1
            counts["total"] = len(self.accounts)
            return counts

    def update_stats(self, email: str, **stats: Any) -> None:
        with self._lock:
            row = self.accounts.get(email)
            if row is None:
                return
            for key in ("total_zaps", "daily_zaps", "streak", "boost", "banned"):
                if key in stats:
                    setattr(row, key, stats[key])

    def poll_stats(self, max_accounts: int = 0, workers: int = 8) -> int:
        """Fetch server stats (total/daily/streak/boost/banned) for accounts.

        Bounded to `max_accounts` (0 = all) and run in a small thread pool so the
        UI is never blocked. Returns how many rows were updated.
        """
        from concurrent.futures import ThreadPoolExecutor

        from ..client import ConsoClient
        from ..config import Settings
        from ..earnings import get_account_row
        from ..session import SessionManager
        from ..storage import Store

        settings = Settings.from_env()
        store = Store(settings.data_dir)
        recs = [r for r in store.all() if r.email]
        if max_accounts:
            recs = recs[:max_accounts]
        manager = SessionManager(settings, store)
        updated = 0

        def _one(rec) -> None:
            nonlocal updated
            client = ConsoClient(settings)
            try:
                client.session = manager.ensure_session(rec).session
                row = get_account_row(client)
                if not row:
                    return
                self.update_stats(
                    rec.email,
                    total_zaps=float(row.get("total_zaps") or 0),
                    daily_zaps=float(row.get("daily_zaps_earned") or 0),
                    streak=int(row.get("current_streak") or 0),
                    boost=float(row.get("boost_factor") or 1.0),
                    banned=bool(row.get("is_banned")),
                )
                updated += 1
            except Exception:
                pass
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(_one, recs))
        return updated


_state: AppState | None = None
_state_lock = threading.Lock()


def get_app_state() -> AppState:
    global _state
    with _state_lock:
        if _state is None:
            _state = AppState()
        return _state
