"""Conso Farm TUI — a Textual control center.

Main menu (M):
  R  Register   — provision N new accounts (then auto-earn)
  D  Daily task — missions + turns for existing accounts
  M  Monitor    — Overview / Accounts / Logs

Views:
  1 Overview   live batch + metrics
  2 Accounts   account table (masked)
  L Logs       realtime event stream

Run:  python -m conso.tui.app   (or: python main.py dashboard)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog, Static

from .state import Sym, get_app_state, mask_email


class Metric(Static):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label
        self.value = "—"

    def update_value(self, value: str) -> None:
        self.value = value
        self.update(f"[b]{self.label}[/b]\n[cyan]{value}[/cyan]")


class OverviewView(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("CONSO FARM — Mission Control", id="title")
        with Horizontal(id="metrics"):
            yield Metric("STATUS")
            yield Metric("PROGRESS")
            yield Metric("ACTIVE")
            yield Metric("FAILED")
            yield Metric("ZAPS")
            yield Metric("RATE/min")
        yield Static(id="detail")

    def refresh_view(self) -> None:
        st = get_app_state()
        b = st.snapshot_batch()
        m = st.metrics()
        tiles = list(self.query(Metric))
        vals = [
            b.status,
            f"{b.done}/{b.target}" if b.target else "—",
            str(sum(1 for a in st.snapshot_accounts() if a.status == "active")),
            str(b.failed),
            f"{m['zaps']:.2f}",
            f"{b.rate:.1f}",
        ]
        for tile, v in zip(tiles, vals):
            tile.update_value(v)
        self.query_one("#detail", Static).update(
            f"solver: {b.solver or '—'}   referral: {b.referral or '—'}   "
            f"elapsed: {b.elapsed:.0f}s   turns: {m['turns']}   missions: {m['missions']}"
        )


class AccountsView(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("ACCOUNTS", id="acct-title")
        yield DataTable(id="acct-table")

    def on_mount(self) -> None:
        self.query_one("#acct-table", DataTable).add_columns(
            "ST", "EMAIL", "STAGE", "ZAPS", "PROXY", "NOTE"
        )

    def refresh_view(self) -> None:
        st = get_app_state()
        table = self.query_one("#acct-table", DataTable)
        table.clear()
        icon = {"active": Sym.DONE, "failed": Sym.FAILED, "running": Sym.RUNNING}
        for a in st.snapshot_accounts():
            table.add_row(
                icon.get(a.status, Sym.PENDING),
                mask_email(a.email),
                a.stage or "—",
                f"{a.zaps:.2f}",
                (a.proxy.split("@")[-1] if a.proxy else "—"),
                a.note[:28] if a.note else "—",
            )


class LogsView(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("LOGS", id="log-title")
        yield RichLog(id="log-panel", highlight=True, markup=True)

    def refresh_view(self) -> None:
        st = get_app_state()
        panel = self.query_one("#log-panel", RichLog)
        seen = getattr(self, "_seen", 0)
        logs = st.snapshot_logs(500)
        for ts, msg in logs[seen:]:
            panel.write(Text.from_markup(f"[dim]{int(ts % 100000)}[/dim] {msg}"))
        self._seen = len(logs)


@dataclass
class RunOptions:
    kind: str          # "register" | "daily"
    count: int = 0
    turns: int = 10
    workers: int = 1
    earn: bool = True
    only_new: bool = False
    solver_concurrent: int = 0


class MenuScreen(ModalScreen[RunOptions | None]):
    """Start menu: Register / Daily task / Monitor."""

    CSS = """
    MenuScreen { align: center middle; background: #0d1117cc; }
    #menu { width: 60; height: auto; border: round #58a6ff; background: #161b22; padding: 1 2; }
    #menu Label { padding: 0 0 1 0; color: #e6edf3; }
    #menu Input { margin: 0 0 1 0; }
    #menu Horizontal { height: auto; }
    #menu Button { margin: 0 1 0 0; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="menu"):
            yield Label("[b]CONSO FARM — pilih aksi[/b]")
            yield Label("R = Register ulang (akun baru)   D = Daily task (earn)   M = Monitor")
            yield Label("Jumlah akun (register):")
            yield Input(value="8", id="count", type="integer")
            yield Label("Turns per akun:")
            yield Input(value="10", id="turns", type="integer")
            yield Label("Farm workers (paralel):")
            yield Input(value="2", id="workers", type="integer")
            yield Label("Solver concurrent (0=env):")
            yield Input(value="0", id="solver", type="integer")
            yield Label("Only-new (earn akun baru saja)? ketik 1=ya / 0=semua:")
            yield Input(value="0", id="onlynew", type="integer")
            with Horizontal():
                yield Button("Register", id="btn-reg", variant="primary")
                yield Button("Daily task", id="btn-daily", variant="success")
                yield Button("Monitor", id="btn-monitor")

    def _opts(self, kind: str) -> RunOptions:
        def _int(id_: str, default: int) -> int:
            try:
                return int(self.query_one(f"#{id_}", Input).value or default)
            except ValueError:
                return default

        return RunOptions(
            kind=kind,
            count=_int("count", 8),
            turns=_int("turns", 10),
            workers=max(1, _int("workers", 2)),
            solver_concurrent=_int("solver", 0),
            only_new=_int("onlynew", 0) == 1,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        kind = {"btn-reg": "register", "btn-daily": "daily", "btn-monitor": "monitor"}[event.button.id]
        self.dismiss(self._opts(kind) if kind != "monitor" else RunOptions(kind="monitor"))


class ConsoTUI(App):
    TITLE = "CONSO FARM"
    SUB_TITLE = "Account Factory"

    CSS = """
    Screen { background: #0d1117; color: #e6edf3; }
    #title { padding: 1 2; text-style: bold; color: #58a6ff; }
    #metrics { height: 5; padding: 0 1; }
    Metric { border: round #30363d; width: 1fr; height: 4; padding: 0 1; margin: 0 1; }
    #detail { padding: 1 2; color: #8b949e; }
    AccountsView, LogsView { height: 1fr; }
    #acct-title, #log-title { padding: 0 2; color: #58a6ff; text-style: bold; }
    DataTable { height: 1fr; }
    RichLog { height: 1fr; }
    Footer { background: #161b22; color: #8b949e; }
    """

    BINDINGS = [
        Binding("1", "show('overview')", "Overview", priority=True),
        Binding("2", "show('accounts')", "Accounts", priority=True),
        Binding("l", "show('logs')", "Logs", priority=True),
        Binding("m", "menu", "Menu", priority=True),
        Binding("q", "quit", "Quit", priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield OverviewView(id="overview")
        yield AccountsView(id="accounts")
        yield LogsView(id="logs")
        yield Footer()

    def on_mount(self) -> None:
        self._active = "overview"
        self._apply_visibility()
        self.set_interval(1.0, self._tick)
        self.call_after_refresh(self.action_menu)

    def _apply_visibility(self) -> None:
        for name in ("overview", "accounts", "logs"):
            self.query_one(f"#{name}").display = (name == self._active)

    def action_show(self, name: str) -> None:
        self._active = name
        self._apply_visibility()
        self._refresh_active()

    def action_menu(self) -> None:
        def _handle(result: RunOptions | None) -> None:
            if result is None:
                return
            if result.kind == "register":
                self._run_register(result)
            elif result.kind == "daily":
                self._run_daily(result)

        self.push_screen(MenuScreen(), _handle)

    # -- background runners ------------------------------------------------
    def _run_register(self, opts: RunOptions) -> None:
        from ..orchestrator import Orchestrator, PipelineConfig
        from ..config import Settings

        settings = Settings.from_env()
        cfg = PipelineConfig(register_count=opts.count, earn=opts.earn, turns=opts.turns,
                             workers=opts.workers, only_new=opts.only_new,
                             solver_concurrent=opts.solver_concurrent)

        def work() -> None:
            Orchestrator(settings, config=cfg, logger=self._log_to_bus).run()

        threading.Thread(target=work, daemon=True).start()
        self.action_show("overview")

    def _run_daily(self, opts: RunOptions) -> None:
        from ..scheduler import DailyLoop, LoopConfig
        from ..config import Settings
        from ..captcha import build_solver
        from ..transport import Transport
        from ..verifiers import build_verifier
        from ..storage import Store

        settings = Settings.from_env()

        def work() -> None:
            store = Store(settings.data_dir)
            runner = DailyLoop(
                settings, store,
                config=LoopConfig(turns=opts.turns, parallel_workers=opts.workers, max_cycles=1),
                solver=build_solver(Transport(settings)),
                verifier=build_verifier(settings), logger=self._log_to_bus,
            )
            runner.run_cycle()

        threading.Thread(target=work, daemon=True).start()
        self.action_show("overview")

    def _log_to_bus(self, message: str) -> None:
        from .events import EventType, get_event_bus

        get_event_bus().emit(EventType.LOG, message=message)

    def _refresh_active(self) -> None:
        widget = self.query_one(f"#{self._active}")
        if hasattr(widget, "refresh_view"):
            widget.refresh_view()

    def _tick(self) -> None:
        self._refresh_active()


def run() -> None:
    ConsoTUI().run()


if __name__ == "__main__":
    run()
