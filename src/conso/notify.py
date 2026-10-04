"""Optional Telegram delivery for run summaries.

Configure in `.env`:

    TELEGRAM_BOT_TOKEN=123456:ABC-...
    TELEGRAM_CHAT_ID=-1001234567890

When both are set, `register`, `pipeline` and `loop` send a short summary
after the run. Nothing is sent when either is missing, and a delivery failure
is logged and swallowed — a notification problem must never fail a run.

This module is transport-only: it never reads the account store, so no tokens
or emails end up in the message.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from typing import Any, Iterable

API = "https://api.telegram.org"
TIMEOUT = 15
# Telegram rejects messages longer than this.
MAX_CHARS = 4000


class NotifierError(RuntimeError):
    """Raised when a message cannot be delivered."""


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                and os.environ.get("TELEGRAM_CHAT_ID", "").strip())


def send(text: str, *, parse_mode: str = "") -> bool:
    """POST one message. Returns True on success, False if not configured.

    Raises NotifierError on a real transport/API failure so callers can decide
    whether to care (the CLI swallows it).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False

    body = text[:MAX_CHARS]
    payload: dict[str, Any] = {"chat_id": chat_id, "text": body}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/bot{token}/sendMessage", data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001 - caller decides
        raise NotifierError(f"telegram send failed: {exc}") from exc


def summary(title: str, rows: Iterable[tuple[str, Any]], *, footer: str = "") -> str:
    """Render an operator-readable summary (plain text, no Markdown)."""
    lines = [f"{title}", ""]
    for label, value in rows:
        lines.append(f"{label:<14} {value}")
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def notify(title: str, rows: Iterable[tuple[str, Any]], *, footer: str = "",
           logger=None) -> bool:
    """Send a summary if Telegram is configured. Never raises."""
    if not is_configured():
        return False
    try:
        ok = send(summary(title, rows, footer=footer))
        if logger and ok:
            logger("notify: telegram summary sent")
        return ok
    except NotifierError as exc:
        if logger:
            logger(f"notify: {exc}")
        return False
