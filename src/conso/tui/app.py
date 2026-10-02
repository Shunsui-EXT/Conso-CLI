"""Conso Farm TUI — a Textual control center.

Views:
  1 Overview   live batch + metrics
  2 Accounts   account table (masked)
  L Logs       realtime event stream

Run:  python -m conso.tui.app
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from .state import Sym, get_app_state, mask_email


class Metric(Static):
    """One metric tile."""

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
        tiles = self.query(Metric)
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
        detail = self.query_one("#detail", Static)
        detail.update(
            f"solver: {b.solver or '—'}   referral: {b.referral or '—'}   "
            f"elapsed: {b.elapsed:.0f}s   turns: {m['turns']}   missions: {m['missions']}"
        )


class AccountsView(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("ACCOUNTS", id="acct-title")
        yield DataTable(id="acct-table")

    def on_mount(self) -> None:
        table = self.query_one("#acct-table", DataTable)
        table.add_columns("STATUS", "EMAIL", "STAGE", "ZAPS", "PROXY", "NOTE")

    def refresh_view(self) -> None:
        st = get_app_state()
        table = self.query_one("#acct-table", DataTable)
        table.clear()
        icon = {"active": Sym.DONE, "failed": Sym.FAILED, "running": Sym.RUNNING}
        for a in st.snapshot_accounts():
            proxy = a.proxy.split("@")[-1] if a.proxy else "—"
            table.add_row(
                icon.get(a.status, Sym.PENDING),
                mask_email(a.email),
                a.stage or "—",
                f"{a.zaps:.2f}",
                proxy,
                a.note[:30] if a.note else "—",
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

    def _apply_visibility(self) -> None:
        for name in ("overview", "accounts", "logs"):
            widget = self.query_one(f"#{name}")
            widget.display = (name == self._active)

    def action_show(self, name: str) -> None:
        self._active = name
        self._apply_visibility()
        self._refresh_active()

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
