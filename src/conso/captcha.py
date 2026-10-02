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
import threading
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
        retries: int = 8,
        proxy_file: str = "",
        proxies: list[str] | None = None,
        proxy_timeout: float = 45.0,
    ) -> None:
        self.transport = transport
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.real_page = real_page
        self.redirect_uri = redirect_uri
        self.verify_url = verify_url
        self.verify_payload = verify_payload
        self.retries = retries
        # Cap for a single proxy solve attempt (proxies that cannot pass
        # Turnstile hang until timeout; keep it short so the direct fallback
        # runs quickly).
        self.proxy_timeout = proxy_timeout
        # Circuit breaker: after this many consecutive proxy failures, stop
        # trying proxies for the rest of this solver's life (a pool that cannot
        # pass Turnstile would otherwise add proxy_timeout to every solve).
        self._proxy_fail_streak = 0
        self._proxy_disabled = False
        self.proxy_max_failures = 3
        self._pinned_proxy = ""
        # Minimum seconds between solves (cooldown). Turnstile flags an IP that
        # solves too rapidly; spacing solves keeps the token flow healthy.
        self.solve_delay = float(os.environ.get("SOLVER_SOLVE_DELAY", "0") or 0)
        self._last_solve_at = 0.0
        self._solve_lock = threading.Lock()
        # Rotating proxy list for the sidecar's per-request `proxy` field.
        self._proxies = list(proxies or [])
        if proxy_file and not self._proxies:
            self._proxies = _load_proxies(proxy_file)
        self._proxy_idx = 0

    def _next_proxy(self) -> str:
        """Round-robin the proxy list; '' means solve directly.

        A pinned per-account proxy (if set) takes precedence and is consumed
        once, then the rotating pool resumes. Returns '' permanently once the
        circuit breaker trips (the pool has failed to pass Turnstile
        proxy_max_failures times in a row).
        """
        if self._pinned_proxy:
            proxy = self._pinned_proxy
            self._pinned_proxy = ""
            return proxy
        if self._proxy_disabled or not self._proxies:
            return ""
        proxy = self._proxies[self._proxy_idx % len(self._proxies)]
        self._proxy_idx += 1
        return proxy

    def _report_proxy(self, ok: bool) -> None:
        if ok:
            self._proxy_fail_streak = 0
            return
        self._proxy_fail_streak += 1
        if self._proxy_fail_streak >= self.proxy_max_failures:
            self._proxy_disabled = True

    def pin_account_proxy(self, proxy: str) -> None:
        """Force the next solves to use this proxy first (per-account sticky).

        The account's signup proxy is tried before the rotating pool, so the
        solve and the signup originate from the same IP.
        """
        self._pinned_proxy = proxy or ""
        if proxy:
            # reset the breaker: a fresh account deserves a fresh proxy try
            self._proxy_disabled = False
            self._proxy_fail_streak = 0

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
        # Cooldown: space solves out so Cloudflare does not flag the IP.
        if self.solve_delay > 0:
            with self._solve_lock:
                wait = self._last_solve_at + self.solve_delay - time.time()
                if wait > 0:
                    time.sleep(wait)
                self._last_solve_at = time.time()
        last_error = ""
        for _attempt in range(1, self.retries + 1):
            proxy = self._next_proxy()
            if proxy:
                # Give the proxy a short leash: proxies that cannot pass
                # Turnstile hang until the full timeout, so cap the proxy
                # attempt and fall back to a direct solve quickly.
                token, err = self._solve_once(
                    sitekey, page_url, min(timeout, self.proxy_timeout), proxy
                )
                last_error = err
                self._report_proxy(bool(token))
                if token:
                    return token
                # Fall back to a direct solve for this attempt.
                token, err = self._solve_once(sitekey, page_url, timeout, "")
                if token:
                    return token
                last_error = err
            else:
                token, err = self._solve_once(sitekey, page_url, timeout, "")
                if token:
                    return token
                last_error = err
        raise RuntimeError(f"turnstile solve failed after {self.retries} attempts: {last_error}")

    def _solve_once(self, sitekey: str, page_url: str, timeout: float,
                    proxy: str) -> tuple[str, str]:
        """One /solve call. Returns (token, error) — token '' on failure."""
        body: dict[str, Any] = {"type": "turnstile", "sitekey": sitekey, "timeout_s": int(timeout)}
        if self.verify_url and self.verify_payload:
            body["url"] = self._page_url(page_url)
            body["verify_url"] = self.verify_url
            body["verify_payload"] = self.verify_payload
        elif self.real_page:
            body["real_page"] = True
            body["url"] = self._page_url(page_url)
        else:
            body["url"] = page_url
        if proxy:
            body["proxy"] = proxy
        # The sidecar is a LOCAL service (127.0.0.1). Never route the /solve
        # call itself through the HTTP proxy pool — the sidecar rejects proxied
        # requests ("403 ... you are not allowed to use the proxy"). Direct only.
        resp = self.transport.request(
            "POST", f"{self.base_url}/solve", headers=self._headers(), json=body,
            retries=0, timeout=timeout + 30, no_proxy=True,
        )
        if resp.status_code != 200:
            return "", f"http {resp.status_code}: {resp.text[:120]}"
        data = _safe_json(resp) or {}
        token = data.get("token")
        if token:
            return token, ""
        return "", data.get("error", "no token")


def _load_proxies(path: str) -> list[str]:
    """Load a proxy list (one per line); strips CRLF and blanks."""
    import os

    if not path or not os.path.exists(path):
        return []
    out: list[str] = []
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            entry = line.strip()
            if entry and not entry.startswith("#"):
                out.append(entry)
    return out


def _safe_json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


class CapsolverApiSolver:
    """Capsolver's own managed API (AntiTurnstileTaskProxyLess) — pure HTTP.

    Unlike the `service` sidecar, this runs no local browser: the request and
    response are plain HTTPS calls to Capsolver. This is the browser-free path
    for the Turnstile gate. The sitekey/URL are the Conso verify-human values,
    so the returned token is accepted by Supabase.
    """

    BASE = "https://api.capsolver.com"

    def __init__(self, api_key: str, transport: Transport, *, sitekey: str = TURNSTILE_SITEKEY,
                 page_url: str = TURNSTILE_PAGE_URL, proxy: str = "") -> None:
        self.api_key = api_key
        self.transport = transport
        self.sitekey = sitekey
        self.page_url = page_url
        self.proxy = proxy

    def solve_turnstile(self, *, sitekey: str = TURNSTILE_SITEKEY,
                        page_url: str = TURNSTILE_PAGE_URL, timeout: float = 120.0) -> str:
        task: dict[str, Any] = {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": sitekey,
        }
        if self.proxy:
            task["type"] = "AntiTurnstileTask"
            task["proxy"] = self.proxy
        created = self.transport.request(
            "POST", f"{self.BASE}/createTask",
            json={"clientKey": self.api_key, "task": task}, retries=1,
        ).json()
        if created.get("errorId"):
            raise RuntimeError(f"capsolver createTask: {created.get('errorDescription')}")
        task_id = created.get("taskId")
        if not task_id:
            raise RuntimeError(f"capsolver createTask: no taskId ({created})")

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            result = self.transport.request(
                "POST", f"{self.BASE}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id}, retries=1,
            ).json()
            if result.get("errorId"):
                raise RuntimeError(f"capsolver getTaskResult: {result.get('errorDescription')}")
            if result.get("status") == "ready":
                token = (result.get("solution") or {}).get("token")
                if not token:
                    raise RuntimeError("capsolver returned no token")
                return token
        raise TimeoutError("capsolver turnstile solve timed out")


class InternalSolverAdapter:
    """Turnstile solver running Camoufox in-process (no sidecar).

    Wraps `internal_solver.InternalTurnstileSolver` behind the CaptchaSolver
    protocol. Solves on the real verify-human page (Conso rejects stub tokens).
    """

    def __init__(self, *, headless: bool = True, timeout: float = 120.0,
                 real_page: bool = False) -> None:
        from .internal_solver import get_default_solver

        self._get = get_default_solver
        self._headless = headless
        self._timeout = timeout
        self._real_page = real_page

    def solve_turnstile(self, *, sitekey: str = TURNSTILE_SITEKEY,
                        page_url: str = TURNSTILE_PAGE_URL, timeout: float = 120.0) -> str:
        solver = self._get(headless=self._headless, timeout_seconds=int(timeout))
        # The Conso verify-human page needs a whitelisted redirect_uri to render.
        if "redirect_uri=" not in page_url:
            sep = "&" if "?" in page_url else "?"
            from urllib.parse import quote
            page_url = f"{page_url}{sep}redirect_uri={quote(CONSO_REDIRECT_URIS[0], safe='')}"
        return solver.solve(
            page_url, sitekey, timeout_seconds=int(timeout), real_page=self._real_page,
        )


def build_solver(transport: Transport) -> CaptchaSolver:
    """Factory driven by env.

    CAPTCHA_PROVIDER:
      internal   -> in-process Camoufox (no sidecar, no browser service)
      none       -> NoopSolver (needs CAPTCHA_TOKEN for auth steps)
      capsolver  -> Capsolver managed API      (BROWSER-FREE, pure HTTP)
      2captcha   -> 2Captcha managed API       (BROWSER-FREE, pure HTTP)
      manual     -> operator pastes a token
      service    -> self-hosted captcha-solver sidecar (uses a local browser)
    """
    provider = os.environ.get("CAPTCHA_PROVIDER", "none").strip().lower()
    if provider == "internal":
        return InternalSolverAdapter(
            headless=os.environ.get("SOLVER_HEADLESS", "1") != "0",
        )
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
            proxy_file=os.environ.get("SOLVER_PROXY_FILE", ""),
        )
    if provider == "capsolver":
        key = os.environ.get("CAPSOLVER_API_KEY", "")
        if os.environ.get("CAPSOLVER_MODE", "api") == "sdk":
            return CapSolverSolver(key, transport)
        return CapsolverApiSolver(
            key, transport,
            proxy=os.environ.get("CAPSOLVER_PROXY", ""),
        )
    if provider == "2captcha":
        return TwoCaptchaSolver(os.environ.get("TWOCAPTCHA_API_KEY", ""), transport)
    if provider == "manual":
        return ManualSolver()
    return NoopSolver(os.environ.get("CAPTCHA_TOKEN", ""))
