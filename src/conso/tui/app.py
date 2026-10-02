"""Conso Farm TUI — a Textual control center.

Main menu (M):
  R  Register   — provision N new accounts (then auto-earn)
  D  Daily task — missions + turns for existing accounts
  M  Monitor    — Overview / Accounts / Logs

Views:
  1 Overview   live batch + progress bar + counts + metrics
  2 Accounts   account table (masked) with filters
  L Logs       realtime event stream

Run:  python -m conso.tui.app   (or: python main.py dashboard)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
)

from .state import Sym, get_app_state, mask_email


class Metric(Static):
    def __init__(self, label: str, color: str = "cyan") -> None:
        super().__init__()
        self.label = label
        self.color = color
        self.value = "—"

    def update_value(self, value: str) -> None:
        self.value = value
        self.update(f"[b]{self.label}[/b]\n[{self.color}]{value}[/{self.color}]")


class OverviewView(VerticalScroll):
    def compose(self) -> ComposeResult:
        yield Static("CONSO FARM — Mission Control", id="title")
        with Horizontal(id="metrics"):
            yield Metric("STATUS", "yellow")
            yield Metric("PROGRESS")
            yield Metric("ACTIVE", "green")
            yield Metric("FAILED", "red")
            yield Metric("ZAPS", "magenta")
            yield Metric("RATE/min", "cyan")
        yield ProgressBar(total=100, show_eta=False, id="progress")
        yield Static(id="detail")
        yield Static(id="counts")

    def refresh_view(self) -> None:
        st = get_app_state()
        b = st.snapshot_batch()
        m = st.metrics()
        counts = st.account_counts()

        tiles = list(self.query(Metric))
        pct = (b.done / b.target * 100) if b.target else 0.0
        vals = [
            b.status,
            f"{b.done}/{b.target}" if b.target else "—",
            str(counts.get("active", 0)),
            str(b.failed),
            f"{m['zaps']:.2f}",
            f"{b.rate:.1f}",
        ]
        for tile, v in zip(tiles, vals):
            tile.update_value(v)

        bar = self.query_one("#progress", ProgressBar)
        bar.update(total=100, progress=min(100, pct))

        self.query_one("#detail", Static).update(
            f"[b]solver[/b] {b.solver or '—'}   [b]referral[/b] {b.referral or '—'}   "
            f"[b]elapsed[/b] {b.elapsed:.0f}s   [b]turns[/b] {m['turns']}   "
            f"[b]missions[/b] {m['missions']}"
        )
        self.query_one("#counts", Static).update(
            f"[b]accounts[/b] {counts.get('total', 0)}  "
            f"[green]active {counts.get('active', 0)}[/green]  "
            f"[yellow]running {counts.get('running', 0)}[/yellow]  "
            f"[red]failed {counts.get('failed', 0)}[/red]  "
            f"[dim]pending {counts.get('pending', 0)}[/dim]"
        )


class AccountsView(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._filter = ""

    def compose(self) -> ComposeResult:
        yield Static("ACCOUNTS", id="acct-title")
        yield Input(placeholder="filter by email/status…", id="acct-filter")
        yield DataTable(id="acct-table")

    def on_mount(self) -> None:
        self.query_one("#acct-table", DataTable).add_columns(
            "ST", "EMAIL", "STAGE", "ZAPS", "PROXY", "NOTE"
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "acct-filter":
            self._filter = event.value.strip().lower()
            self.refresh_view()

    def refresh_view(self) -> None:
        st = get_app_state()
        table = self.query_one("#acct-table", DataTable)
        table.clear()
        icon = {"active": Sym.DONE, "failed": Sym.FAILED, "running": Sym.RUNNING}
        rows = st.snapshot_accounts()
        if self._filter:
            rows = [a for a in rows
                    if self._filter in a.email.lower() or self._filter in a.status.lower()]
        # show failures first, then running, then the rest
        order = {"failed": 0, "running": 1, "active": 2, "pending": 3}
        rows.sort(key=lambda a: order.get(a.status, 9))
        for a in rows:
            table.add_row(
                icon.get(a.status, Sym.PENDING),
                mask_email(a.email),
                a.stage or "—",
                f"{a.zaps:.2f}",
                (a.proxy.split("@")[-1] if a.proxy else "—"),
                a.note[:32] if a.note else "—",
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
    kind: str          # "register" | "daily" | "monitor"
    count: int = 0
    turns: int = 10
    workers: int = 1
    earn: bool = True
    only_new: bool = False
    solver_concurrent: int = 0
    referral: str = ""


class MenuScreen(ModalScreen[RunOptions | None]):
    """Start menu: Register / Daily task / Monitor, with referral input."""

    CSS = """
    MenuScreen { align: center middle; background: #0d1117cc; }
    #menu { width: 68; height: auto; max-height: 90%; border: round #58a6ff;
            background: #161b22; padding: 1 2; }
    #menu Label { padding: 0 0 1 0; color: #e6edf3; }
    #menu Input { margin: 0 0 1 0; }
    #menu Horizontal { height: auto; }
    #menu Button { margin: 0 1 0 0; }
    #hint { color: #8b949e; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="menu"):
            yield Label("[b]CONSO FARM — pilih aksi[/b]")
            yield Label("[dim]Register = akun baru + earn · Daily = earn akun lama · Monitor = lihat saja[/dim]")
            yield Label("Jumlah akun (register):")
            yield Input(value="8", id="count", type="integer")
            yield Label("Turns per akun:")
            yield Input(value="10", id="turns", type="integer")
            yield Label("Farm workers (paralel):")
            yield Input(value="2", id="workers", type="integer")
            yield Label("Solver concurrent (0 = dari .env):")
            yield Input(value="0", id="solver", type="integer")
            yield Label("Referral code (kosong = dari .env):")
            yield Input(value="", id="referral", placeholder="CONSO-XXXXX")
            yield Label("Only-new (earn akun baru saja)? 1=ya / 0=semua:")
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

        referral = self.query_one("#referral", Input).value.strip()
        return RunOptions(
            kind=kind,
            count=max(1, _int("count", 8)),
            turns=max(1, _int("turns", 10)),
            workers=max(1, _int("workers", 2)),
            solver_concurrent=_int("solver", 0),
            only_new=_int("onlynew", 0) == 1,
            referral=referral,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        kind = {"btn-reg": "register", "btn-daily": "daily", "btn-monitor": "monitor"}[event.button.id]
        self.dismiss(RunOptions(kind="monitor") if kind == "monitor" else self._opts(kind))


class ConsoTUI(App):
    TITLE = "CONSO FARM"
    SUB_TITLE = "Account Factory"

    CSS = """
    Screen { background: #0d1117; color: #e6edf3; }
    #title { padding: 1 2; text-style: bold; color: #58a6ff; }
    #metrics { height: 5; padding: 0 1; }
    Metric { border: round #30363d; width: 1fr; height: 4; padding: 0 1; margin: 0 1; }
    #progress { margin: 1 2; }
    #detail { padding: 1 2; color: #8b949e; }
    #counts { padding: 0 2 1 2; }
    AccountsView, LogsView { height: 1fr; }
    #acct-title, #log-title { padding: 0 2; color: #58a6ff; text-style: bold; }
    #acct-filter { margin: 0 2 1 2; }
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
        # seed the table with existing accounts
        try:
            from ..config import Settings
            from ..storage import Store

            get_app_state().load_store(Store(Settings.from_env().data_dir))
        except Exception:
            pass
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
        from ..config import Settings
        from ..orchestrator import Orchestrator, PipelineConfig

        settings = Settings.from_env()
        cfg = PipelineConfig(
            register_count=opts.count, earn=opts.earn, turns=opts.turns,
            workers=opts.workers, only_new=opts.only_new,
            solver_concurrent=opts.solver_concurrent, referral=opts.referral,
        )

        def work() -> None:
            Orchestrator(settings, config=cfg, logger=self._log_to_bus).run()

        threading.Thread(target=work, daemon=True).start()
        self.action_show("overview")

    def _run_daily(self, opts: RunOptions) -> None:
        from ..captcha import build_solver
        from ..config import Settings
        from ..scheduler import DailyLoop, LoopConfig
        from ..storage import Store
        from ..transport import Transport
        from ..verifiers import build_verifier

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
