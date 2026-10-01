"""
Pluggable verification adapters.

If the Supabase project has `mailer_autoconfirm` disabled, a new account must
confirm its email (or phone). The registration pipeline is decoupled from the
verification mechanism through the `EmailVerifier` protocol, so any of these
can be swapped in without touching the pipeline:

  - NoopVerifier       : account is auto-confirmed, nothing to do.
  - ImapVerifier       : poll an IMAP mailbox (catch-all) for the confirm link.
  - TempMailVerifier   : poll a TempMail-style HTTP API.
  - WebhookVerifier    : wait for a link pushed by an external relay.
"""

from __future__ import annotations

import email
import imaplib
import re
import time
from typing import Protocol

from .transport import Transport

LINK_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
OTP_RE = re.compile(r"\b(\d{6})\b")


class EmailVerifier(Protocol):
    def wait_for_link(self, address: str, *, timeout: float = 120.0) -> str | None: ...


class NoopVerifier:
    """For auto-confirm projects: nothing to fetch."""

    def wait_for_link(self, address: str, *, timeout: float = 120.0) -> str | None:
        return None


class ImapVerifier:
    """Poll an IMAP mailbox for a confirmation link addressed to `address`."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        *,
        port: int = 993,
        folder: str = "INBOX",
        sender_filter: str = "",
    ) -> None:
        self.host = host
        self.user = user
        self.password = password
        self.port = port
        self.folder = folder
        self.sender_filter = sender_filter.lower()

    def wait_for_link(self, address: str, *, timeout: float = 120.0) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            link = self._poll(address)
            if link:
                return link
            time.sleep(3)
        return None

    def _poll(self, address: str) -> str | None:
        try:
            with imaplib.IMAP4_SSL(self.host, self.port) as conn:
                conn.login(self.user, self.password)
                conn.select(self.folder)
                typ, data = conn.search(None, "TO", f'"{address}"')
                if typ != "OK" or not data or not data[0]:
                    return None
                for num in reversed(data[0].split()[-10:]):
                    typ, msg_data = conn.fetch(num, "(RFC822)")
                    if typ != "OK" or not msg_data:
                        continue
                    raw = msg_data[0][1]
                    message = email.message_from_bytes(raw)
                    sender = str(message.get("From", "")).lower()
                    if self.sender_filter and self.sender_filter not in sender:
                        continue
                    body = _extract_body(message)
                    match = LINK_RE.search(body)
                    if match:
                        return match.group(0)
        except (imaplib.IMAP4.error, OSError):
            return None
        return None


class TempMailVerifier:
    """Poll a TempMail-compatible HTTP API (e.g. mail.tm / 1secmail)."""

    def __init__(self, transport: Transport, api_base: str, api_key: str = "") -> None:
        self.transport = transport
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def wait_for_link(self, address: str, *, timeout: float = 120.0) -> str | None:
        deadline = time.time() + timeout
        seen: set[str] = set()
        while time.time() < deadline:
            link = self._poll(address, seen)
            if link:
                return link
            time.sleep(3)
        return None

    def _poll(self, address: str, seen: set[str]) -> str | None:
        try:
            resp = self.transport.request(
                "GET", f"{self.api_base}/messages", headers=self._headers()
            )
            messages = resp.json() if resp.status_code == 200 else []
        except Exception:
            return None
        for message in messages if isinstance(messages, list) else []:
            msg_id = str(message.get("id") or message.get("uid") or "")
            if not msg_id or msg_id in seen:
                continue
            seen.add(msg_id)
            body = _collect_strings(message)
            match = LINK_RE.search(body) or OTP_RE.search(body)
            if match:
                return match.group(0)
        return None


def _extract_body(message: email.message.Message) -> str:
    chunks: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    chunks.append(payload.decode(part.get_content_charset() or "utf-8", "ignore"))
    else:
        payload = message.get_payload(decode=True)
        if payload:
            chunks.append(payload.decode(message.get_content_charset() or "utf-8", "ignore"))
    return "\n".join(chunks)


def _collect_strings(obj: object) -> str:
    out: list[str] = []
    if isinstance(obj, dict):
        for value in obj.values():
            out.append(_collect_strings(value))
    elif isinstance(obj, list):
        for value in obj:
            out.append(_collect_strings(value))
    elif isinstance(obj, str):
        out.append(obj)
    return "\n".join(out)


def build_verifier(settings) -> EmailVerifier:  # noqa: ANN001 - Settings, avoids import cycle
    """Factory driven by env: VERIFIER = none|imap|tempmail."""
    import os

    kind = os.environ.get("VERIFIER", "none").strip().lower()
    if kind == "imap":
        return ImapVerifier(
            host=os.environ.get("IMAP_HOST", ""),
            user=os.environ.get("IMAP_USER", ""),
            password=os.environ.get("IMAP_PASSWORD", ""),
            port=int(os.environ.get("IMAP_PORT", "993")),
            folder=os.environ.get("IMAP_FOLDER", "INBOX"),
            sender_filter=os.environ.get("IMAP_SENDER_FILTER", ""),
        )
    if kind == "tempmail":
        return TempMailVerifier(
            Transport(settings),
            api_base=os.environ.get("TEMPMAlL_API", os.environ.get("TEMPMAIL_API", "")),
            api_key=os.environ.get("TEMPMAIL_KEY", ""),
        )
    return NoopVerifier()
