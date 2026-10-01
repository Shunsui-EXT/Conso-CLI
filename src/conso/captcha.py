"""
CAPTCHA solver interface.

The Conso Supabase project enforces Cloudflare Turnstile on every auth
endpoint (signup, password login, OTP). The Turnstile widget is hosted at
`https://www.conso.xyz/verify-human` with sitekey `0x4AAAAAAEzmjKoKI6TA61_6`.

A valid token must be supplied as `gotrue_meta_security.captcha_token` on the
auth request. This module is a pluggable adapter: any provider that returns a
Turnstile token works, so the registration pipeline is decoupled from the
solver backend.

Providers:
  - NoopSolver       : returns a configured static token (for testing).
  - CapSolverSolver  : https://api.capsolver.com  (AntiTurnstileTaskProxyLess)
  - TwoCaptchaSolver : https://2captcha.com       (method=turnstile)
  - ManualSolver     : prints the URL and waits for a pasted token.

Note: id_token login (`sign_in_id_token`) bypasses the captcha entirely, so a
real Google/OIDC id_token is an alternative to solving Turnstile.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

from .transport import Transport

TURNSTILE_SITEKEY = "0x4AAAAAAEzmjKoKI6TA61_6"
TURNSTILE_PAGE_URL = "https://www.conso.xyz/verify-human"


class CaptchaSolver(Protocol):
    def solve_turnstile(
        self, *, sitekey: str = TURNSTILE_SITEKEY, page_url: str = TURNSTILE_PAGE_URL,
        timeout: float = 120.0,
    ) -> str: ...


class NoopSolver:
    """Returns a fixed token (used when a token is supplied externally)."""

    def __init__(self, token: str = "") -> None:
        self.token = token

    def solve_turnstile(self, *, sitekey: str = TURNSTILE_SITEKEY,
                        page_url: str = TURNSTILE_PAGE_URL, timeout: float = 120.0) -> str:
        if not self.token:
            raise RuntimeError("NoopSolver has no token configured (set CAPTCHA_TOKEN).")
        return self.token


class CapSolverSolver:
    """CapSolver anti-turnstile, proxyless."""

    BASE = "https://api.capsolver.com"

    def __init__(self, api_key: str, transport: Transport) -> None:
        self.api_key = api_key
        self.transport = transport

    def solve_turnstile(self, *, sitekey: str = TURNSTILE_SITEKEY,
                        page_url: str = TURNSTILE_PAGE_URL, timeout: float = 120.0) -> str:
        task = {
            "clientKey": self.api_key,
            "task": {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": sitekey,
            },
        }
        resp = self.transport.request("POST", f"{self.BASE}/createTask", json=task)
        created = resp.json()
        if created.get("errorId"):
            raise RuntimeError(f"capsolver createTask: {created.get('errorDescription')}")
        task_id = created["taskId"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            resp = self.transport.request(
                "POST", f"{self.BASE}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
            )
            result = resp.json()
            if result.get("errorId"):
                raise RuntimeError(f"capsolver getTaskResult: {result.get('errorDescription')}")
            if result.get("status") == "ready":
                token = (result.get("solution") or {}).get("token")
                if not token:
                    raise RuntimeError("capsolver returned no token")
                return token
        raise TimeoutError("capsolver turnstile solve timed out")


class TwoCaptchaSolver:
    """2Captcha turnstile (in.php / res.php)."""

    BASE = "https://2captcha.com"

    def __init__(self, api_key: str, transport: Transport) -> None:
        self.api_key = api_key
        self.transport = transport

    def solve_turnstile(self, *, sitekey: str = TURNSTILE_SITEKEY,
                        page_url: str = TURNSTILE_PAGE_URL, timeout: float = 120.0) -> str:
        params = {
            "key": self.api_key,
            "method": "turnstile",
            "sitekey": sitekey,
            "pageurl": page_url,
            "json": 1,
        }
        resp = self.transport.request("POST", f"{self.BASE}/in.php", data=params)
        created = resp.json()
        if created.get("status") != 1:
            raise RuntimeError(f"2captcha in.php: {created.get('request')}")
        task_id = created["request"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            resp = self.transport.request(
                "GET", f"{self.BASE}/res.php",
                data={"key": self.api_key, "action": "get", "id": task_id, "json": 1},
            )
            result = resp.json()
            if result.get("status") == 1:
                return result["request"]
            if result.get("request") != "CAPCHA_NOT_READY":
                raise RuntimeError(f"2captcha res.php: {result.get('request')}")
        raise TimeoutError("2captcha turnstile solve timed out")


class ManualSolver:
    """Prompt the operator for a token obtained manually."""

    def solve_turnstile(self, *, sitekey: str = TURNSTILE_SITEKEY,
                        page_url: str = TURNSTILE_PAGE_URL, timeout: float = 120.0) -> str:
        print(f"Open {page_url} , solve the Turnstile (sitekey {sitekey}), paste the token:")
        return input("captcha_token> ").strip()


# Chromium-app redirect URIs the Conso verify-human page accepts. The page only
# renders the widget when redirect_uri is one of these, so the real-page solve
# must target it. Kept in sync with conso.xyz's verify-human whitelist.
CONSO_REDIRECT_URIS = [
    "https://bjibbmkefnaamkenamdppfengeepadpi.chromiumapp.org/",
    "https://mfjolkgcoehffnccojgdegniohfejfml.chromiumapp.org/",
    "consomobileapp://verify-human",
]


class SolverServiceSolver:
    """Client for a self-hosted captcha-solver sidecar (FastAPI, POST /solve).

    Compatible with waguriagentic/captcha-solver. The Conso Turnstile must be
    solved on the *real* verify-human page (stub tokens are rejected by
    Supabase), so `real_page` defaults to True and the URL is built with a
    whitelisted redirect_uri.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        base_url: str = "http://127.0.0.1:8877",
        token: str = "",
        real_page: bool = True,
        redirect_uri: str = CONSO_REDIRECT_URIS[0],
        verify_url: str = "",
        verify_payload: dict | None = None,
        retries: int = 4,
    ) -> None:
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.real_page = real_page
        self.redirect_uri = redirect_uri
        self.verify_url = verify_url
        self.verify_payload = verify_payload
        self.retries = retries

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _page_url(self, page_url: str) -> str:
        # The verify-human page requires a whitelisted redirect_uri to render.
        if "redirect_uri=" in page_url:
            return page_url
        sep = "&" if "?" in page_url else "?"
        from urllib.parse import quote
        return f"{page_url}{sep}redirect_uri={quote(self.redirect_uri, safe='')}"

    def solve_turnstile(self, *, sitekey: str = TURNSTILE_SITEKEY,
                        page_url: str = TURNSTILE_PAGE_URL, timeout: float = 120.0) -> str:
        last_error = ""
        for attempt in range(1, self.retries + 1):
            body: dict[str, Any] = {"type": "turnstile", "sitekey": sitekey, "timeout_s": int(timeout)}
            if self.verify_url and self.verify_payload:
                # Solve + verify in one shot (recommended for Supabase).
                body["url"] = self._page_url(page_url)
                body["verify_url"] = self.verify_url
                body["verify_payload"] = self.verify_payload
            elif self.real_page:
                body["real_page"] = True
                body["url"] = self._page_url(page_url)
            else:
                body["url"] = page_url
            resp = self.transport.request(
                "POST", f"{self.base_url}/solve", headers=self._headers(), json=body,
                retries=0, timeout=timeout + 30,
            )
            if resp.status_code != 200:
                last_error = f"http {resp.status_code}: {resp.text[:120]}"
                continue
            data = _safe_json(resp) or {}
            token = data.get("token")
            if token:
                return token
            last_error = data.get("error", "no token")
        raise RuntimeError(f"turnstile solve failed after {self.retries} attempts: {last_error}")


def _safe_json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


def build_solver(transport: Transport) -> CaptchaSolver:
    """Factory driven by env.

    CAPTCHA_PROVIDER = none | capsolver | 2captcha | manual | service
      service  -> local/self-hosted sidecar (SOLVER_URL, SOLVER_TOKEN)
    """
    provider = os.environ.get("CAPTCHA_PROVIDER", "none").strip().lower()
    if provider == "service":
        verify_url = os.environ.get("CAPTCHA_VERIFY_URL", "").strip()
        verify_payload = None
        if verify_url:
            verify_payload = {
                "secret": os.environ.get("CAPTCHA_VERIFY_SECRET", ""),
                "response": "__TOKEN__",
            }
        return SolverServiceSolver(
            transport,
            base_url=os.environ.get("SOLVER_URL", "http://127.0.0.1:8877"),
            token=os.environ.get("SOLVER_TOKEN", ""),
            real_page=os.environ.get("SOLVER_REAL_PAGE", "1") == "1",
            redirect_uri=os.environ.get("CONSO_REDIRECT_URI", CONSO_REDIRECT_URIS[0]),
            verify_url=verify_url,
            verify_payload=verify_payload,
        )
    if provider == "capsolver":
        return CapSolverSolver(os.environ.get("CAPSOLVER_API_KEY", ""), transport)
    if provider == "2captcha":
        return TwoCaptchaSolver(os.environ.get("TWOCAPTCHA_API_KEY", ""), transport)
    if provider == "manual":
        return ManualSolver()
    return NoopSolver(os.environ.get("CAPTCHA_TOKEN", ""))
