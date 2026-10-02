"""Branding header for the Conso TUI (mirrors GROK-CLI's banner).

Structure:
  [ ASCII logo ]            (responsive: full / compact / minimal)
      ◆ FARM × Conso automation ◆
  Verifier: temptf · Solver: internal · Transport: curl_cffi
  ───────────────────────────
  DASHBOARD │ VIEW: X │ STATUS: ● RUNNING 12s │ RATE: 2.4/min
"""

from __future__ import annotations

from rich.align import Align
from rich.console import Group, RenderResult
from rich.text import Text
from textual.widget import Widget

LOGO_FULL = [
    " ██████╗ ██████╗ ███╗   ██╗███████╗ ██████╗ ",
    "██╔════╝██╔═══██╗████╗  ██║██╔════╝██╔═══██╗",
    "██║     ██║   ██║██╔██╗ ██║███████╗██║   ██║",
    "██║     ██║   ██║██║╚██╗██║╚════██║██║   ██║",
    "╚██████╗╚██████╔╝██║ ╚████║███████║╚██████╔╝",
    " ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝ ╚═════╝ ",
]

LOGO_COMPACT = [
    " ██████╗ ██████╗ ███╗   ██╗███████╗ ██████╗",
    "██╔════╝██╔═══██╗████╗  ██║██╔════╝██╔═══██╗",
    "██║     ██║   ██║██╔██╗ ██║███████╗██║   ██║",
    "╚██████╗╚██████╔╝██║ ╚████║███████║╚██████╔╝",
]

LOGO_MINIMAL = ["[ C O N S O ]"]


def _logo(width: int) -> list[str]:
    if width >= 100:
        return LOGO_FULL
    if width >= 60:
        return LOGO_COMPACT
    return LOGO_MINIMAL


class AppHeader(Widget):
    """Top branding header: logo + tagline + meta + status line."""

    DEFAULT_CSS = """
    AppHeader {
        width: 100%;
        height: auto;
        padding: 0 0 1 0;
        background: #0d1117;
    }
    """

    def __init__(self, verifier: str = "temptf", solver: str = "internal",
                 transport: str = "curl_cffi", **kwargs) -> None:
        super().__init__(**kwargs)
        self.verifier = verifier
        self.solver = solver
        self.transport = transport

    def render(self) -> RenderResult:
        from .state import get_app_state

        width = self.size.width or 100
        try:
            height = self.screen.size.height
        except Exception:
            height = 35

        st = get_app_state()
        b = st.snapshot_batch()
        items: list = []

        # logo (tighter terminals drop to a one-line mark)
        if height < 28:
            items.append(Align.center(Text("[ C O N S O   F A R M ]", style="bold #58a6ff")))
        else:
            items.append(Align.center(
                Text("\n".join(_logo(width)), style="bold #58a6ff")))
            items.append(Text(""))
            items.append(Align.center(
                Text("◆ FARM × Conso account automation ◆", style="bold bright_white")))
            items.append(Text(""))
            meta = Text()
            meta.append("Verifier: ", style="dim"); meta.append(self.verifier, style="cyan")
            meta.append("  ·  Solver: ", style="dim"); meta.append(self.solver, style="cyan")
            meta.append("  ·  Transport: ", style="dim"); meta.append(self.transport, style="cyan")
            items.append(Align.center(meta))

        # divider
        items.append(Text(""))
        items.append(Align.center(Text("─" * min(width - 4, 76), style="dim")))
        items.append(Text(""))

        # status / nav line
        nav = Text(justify="center")
        nav.append("DASHBOARD", style="bold bright_white")
        nav.append("  │  STATUS: ", style="dim")
        icon, color = {
            "RUNNING": ("●", "bold green"),
            "COMPLETED": ("✓", "bold cyan"),
            "STOPPED": ("■", "bold yellow"),
        }.get(b.status, ("○", "dim"))
        nav.append(f"{icon} {b.status}", style=color)
        if b.status != "IDLE":
            nav.append(f"  {b.done}/{b.target}", style="bold white")
            nav.append(f"  {b.elapsed:.0f}s", style="bold white")
        nav.append("  │  RATE: ", style="dim")
        nav.append(f"{b.rate:.1f}/min", style="bold white")
        nav.append("  │  SOLVER: ", style="dim")
        nav.append(self.solver, style="bold magenta")
        items.append(nav)

        return Group(*items)
