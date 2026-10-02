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
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
)

from .banner import AppHeader
from .state import Sym, get_app_state, mask_email, sparkline


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
    """Adaptive overview: live monitor while a run is active, portfolio health
    when idle."""

    def compose(self) -> ComposeResult:
        yield Static("CONSO FARM — Mission Control", id="title")
        yield Static(id="statusline")
        with Horizontal(id="metrics"):
            yield Metric("RUN ZAPS", "magenta")
            yield Metric("DONE", "green")
            yield Metric("TURNS", "cyan")
            yield Metric("RATE/min", "cyan")
            yield Metric("FAILED", "red")
            yield Metric("MISSIONS", "yellow")
        yield ProgressBar(total=100, show_eta=False, id="progress")
        yield Static(id="spark")
        yield Static(id="detail")
        yield Static(id="platforms")
        yield Static(id="counts")
        yield Static(id="top")

    def refresh_view(self) -> None:
        st = get_app_state()
        st.sample()
        b = st.snapshot_batch()
        if b.status == "RUNNING":
            self._render_running(st, b)
        else:
            self._render_idle(st, b)

    # -- RUNNING: live monitor ---------------------------------------------
    def _render_running(self, st, b) -> None:
        m = st.metrics()
        counts = st.account_counts()
        eta = ""
        if b.rate > 0 and b.target > b.done:
            eta = f"   [dim]ETA {(b.target - b.done) / b.rate:.1f} min[/dim]"
        self.query_one("#statusline", Static).update(
            f"[b green]● RUNNING[/b green]  {b.done}/{b.target} done  "
            f"{b.failed} failed  {b.elapsed:.0f}s{eta}"
        )
        tiles = list(self.query(Metric))
        for tile, v in zip(tiles, [
            f"{st.run_zaps:.2f}", f"{b.done}/{b.target}",
            f"{m['turns']}", f"{b.rate:.1f}", f"{b.failed}", f"{m['missions']}",
        ]):
            tile.update_value(v)

        pct = (b.done / b.target * 100) if b.target else 0.0
        self.query_one("#progress", ProgressBar).update(total=100, progress=min(100, pct))

        self.query_one("#spark", Static).update(
            f"[b]zaps/min[/b] {sparkline(st.zaps_history, 44)}  [dim](avg {b.rate:.1f})[/dim]"
        )
        self.query_one("#detail", Static).update(
            f"[b]solver[/b] {b.solver or '—'}   [b]referral[/b] {b.referral or '—'}   "
            f"[b]elapsed[/b] {b.elapsed:.0f}s   [b]run zaps[/b] {st.run_zaps:.2f}"
        )
        if st.platform_zaps:
            parts = [f"[b]{p}[/b] {z:.1f}[dim]({st.platform_turns.get(p, 0)}t)[/dim]"
                     for p, z in sorted(st.platform_zaps.items(), key=lambda x: -x[1])]
            self.query_one("#platforms", Static).update("[b]platforms[/b] " + "  ".join(parts))
        else:
            self.query_one("#platforms", Static).update("[b]platforms[/b] —")
        self.query_one("#counts", Static).update(
            f"[b]accounts[/b] {counts.get('total', 0)}  "
            f"[green]active {counts.get('active', 0)}[/green]  "
            f"[yellow]running {counts.get('running', 0)}[/yellow]  "
            f"[red]failed {counts.get('failed', 0)}[/red]"
        )
        if st.failed_reasons:
            reasons = "  ".join(f"{k} [red]×{v}[/red]" for k, v in
                                sorted(st.failed_reasons.items(), key=lambda x: -x[1])[:4])
            self.query_one("#top", Static).update(f"[b red]errors[/b red] {reasons}")
        else:
            self.query_one("#top", Static).update("")

    # -- IDLE: portfolio health --------------------------------------------
    def _render_idle(self, st, _b) -> None:
        p = st.portfolio()
        self.query_one("#statusline", Static).update(
            "[b]○ IDLE[/b]   tekan [b]M[/b] untuk menu (Register / Daily task)   ·   "
            "[b]p[/b] refresh stats server"
        )
        tiles = list(self.query(Metric))
        for tile, v in zip(tiles, [
            f"{p['store_zaps']:.0f}", f"{p['accounts']}",
            f"{p['avg_streak']:.1f}", "—", f"{p['banned']}", "—",
        ]):
            tile.update_value(v)

        # cap utilization: avg daily vs the 21 budget
        cap_pct = min(100.0, p["avg_daily"] / 21.0 * 100.0)
        self.query_one("#progress", ProgressBar).update(total=100, progress=cap_pct)

        tops = [a.total_zaps for a in p["top"]]
        self.query_one("#spark", Static).update(
            f"[b]top zaps[/b] {sparkline(tops, 44)}  "
            f"[dim](top {tops[0]:.0f} · med {(sorted(tops)[len(tops)//2] if tops else 0):.0f})[/dim]"
        )
        self.query_one("#detail", Static).update(
            f"[b]avg daily[/b] {p['avg_daily']:.1f}/21 ({cap_pct:.0f}%)   "
            f"[b]avg boost[/b] {p['avg_boost']:.2f}   "
            f"[b]banned[/b] [red]{p['banned']}[/red]"
        )
        self.query_one("#platforms", Static).update("")

        counts = st.account_counts()
        self.query_one("#counts", Static).update(
            f"[b]accounts[/b] {counts.get('total', 0)}  "
            f"[green]active {counts.get('active', 0)}[/green]  "
            f"[red]failed {counts.get('failed', 0)}[/red]  "
            f"[dim]pending {counts.get('pending', 0)}[/dim]"
        )
        if p["top"] and p["top"][0].total_zaps > 0:
            lines = [f"  {i+1}. {mask_email(a.email)}  [b]{a.total_zaps:.1f}[/b] zaps "
                     f"[dim](today {a.daily_zaps:.1f}, streak {a.streak})[/dim]"
                     for i, a in enumerate(p["top"])]
            self.query_one("#top", Static).update("[b]top accounts[/b]\n" + "\n".join(lines))
        else:
            self.query_one("#top", Static).update(
                "[dim]tekan p untuk refresh stats dari server[/dim]"
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
            "ST", "EMAIL", "STAGE", "TOTAL", "TODAY", "STREAK", "BOOST", "PROXY", "NOTE"
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
        order = {"failed": 0, "running": 1, "active": 2, "pending": 3}
        rows.sort(key=lambda a: order.get(a.status, 9))
        for a in rows:
            st_icon = Sym.FAILED if a.banned else icon.get(a.status, Sym.PENDING)
            note = a.note[:26] if a.note else ("BANNED" if a.banned else "—")
            table.add_row(
                st_icon,
                mask_email(a.email),
                a.stage or "—",
                f"{a.total_zaps:.1f}",
                f"{a.daily_zaps:.1f}",
                str(a.streak),
                f"{a.boost:.2f}",
                (a.proxy.split("@")[-1] if a.proxy else "—"),
                note,
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
    #title { padding: 1 2 0 2; text-style: bold; color: #58a6ff; }
    #statusline { padding: 0 2 1 2; }
    #metrics { height: 5; padding: 0 1; }
    Metric { border: round #30363d; width: 1fr; height: 4; padding: 0 1; margin: 0 1; }
    #progress { margin: 1 2; }
    #spark { padding: 0 2; color: #7ee787; }
    #detail { padding: 1 2 0 2; color: #8b949e; }
    #platforms { padding: 1 2 0 2; color: #c9d1d9; }
    #counts { padding: 0 2; }
    #top { padding: 1 2; color: #8b949e; }
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
        Binding("p", "poll_stats", "Refresh stats", priority=True),
        Binding("q", "quit", "Quit", priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader()
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
        # poll server stats every 30s in the background (never blocks the UI)
        self.set_interval(30.0, self._poll_stats_bg)
        self.call_after_refresh(self.action_menu)
        self.call_after_refresh(self._poll_stats_bg)

    def action_poll_stats(self) -> None:
        self._poll_stats_bg()

    def _poll_stats_bg(self) -> None:
        import threading

        from .state import get_app_state as _gas

        def work() -> None:
            try:
                _gas().poll_stats(workers=8)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

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
        try:
            self.query_one(AppHeader).refresh()
        except Exception:
            pass


def run() -> None:
    ConsoTUI().run()


if __name__ == "__main__":
    run()
