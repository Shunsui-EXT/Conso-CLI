"""Turnstile solver backed by CloakBrowser (stealth Chromium).

Alternative to the Camoufox solver in `internal_solver.py`. CloakBrowser is
Playwright-compatible, so the driving code is the same shape: load the real
verify-human page, then render the widget from an explicit onload callback —
the page's own Next.js <Script> render is unreliable in a headless browser.

CloakBrowser's free channel gates concurrent *sessions* through a license
runtime in the Pro binary. The installed 146.0.7680.177.5 (free) build has no
LicenseRuntime symbols at all, so this launches without special handling; a Pro
build that enforces the seat cap needs the binary patched (see
analysis/cloakbrowser-re/REPORT.md) before N instances can run at once.

Env:
    CLOAK_HEADLESS     0 -> headed            (default 1)
    CLOAK_STEALTH      0 -> disable stealth args (default 1)
"""

from __future__ import annotations

import json
import os
import threading
import time
from urllib.parse import urlparse

# Conso verify-human only renders the widget with a whitelisted redirect_uri.
CONSO_REDIRECT_URI = "https://bjibbmkefnaamkenamdppfengeepadpi.chromiumapp.org/"

# Stub page: explicit turnstile.render() from the api.js onload callback. This
# is the path that reliably produces a token; the real page's own script is not.
STUB_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__consoCb" async defer></script>
<script>function __consoCb(){window.turnstile.render("#cf-slot", __CONSO_OPTS__);}</script>
</head><body><div id="cf-slot"></div></body></html>"""


class CloakSolverError(RuntimeError):
    """Raised when the CloakBrowser solver cannot produce a token."""


def is_available() -> bool:
    try:
        import cloakbrowser  # noqa: F401

        return True
    except ImportError:
        return False


def _proxy_config(proxy: str | None):
    """Playwright-style proxy dict, or None."""
    if not proxy:
        return None
    from cloakbrowser import ProxySettings

    parsed = urlparse(proxy)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise CloakSolverError(f"invalid proxy: {proxy}")
    return ProxySettings(
        server=f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        username=parsed.username or "",
        password=parsed.password or "",
    )


class CloakTurnstileSolver:
    """One browser per instance; contexts are created per solve and closed."""

    def __init__(self, *, headless: bool | None = None, timeout_seconds: int = 90,
                 proxy: str | None = None, max_concurrent: int = 2) -> None:
        self.headless = os.environ.get("CLOAK_HEADLESS", "1") != "0" if headless is None else headless
        self.stealth = os.environ.get("CLOAK_STEALTH", "1") == "1"
        self.timeout_seconds = max(10, int(timeout_seconds))
        self.proxy = proxy
        self.max_concurrent = max(1, int(max_concurrent))
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(self.max_concurrent)
        self._pw = None
        self._browser = None

    @property
    def ready(self) -> bool:
        return self._browser is not None

    def warm(self, timeout: float = 60.0) -> bool:
        # `timeout` is accepted for parity with the Camoufox solver; launch is
        # synchronous here so there is nothing to wait on.
        try:
            self._ensure()
            return self.ready
        except Exception:
            return False

    def _ensure(self):
        with self._lock:
            if self._browser is not None:
                return self._browser
            if not is_available():
                raise CloakSolverError(
                    "cloakbrowser is not installed (pip install cloakbrowser && cloakbrowser install)"
                )
            from cloakbrowser import launch

            # launch() returns a ready Browser, not a Playwright wrapper, so
            # contexts are created on it directly.
            self._browser = launch(headless=self.headless, stealth_args=self.stealth,
                                   proxy=_proxy_config(self.proxy))
            return self._browser

    def solve(self, url: str, sitekey: str, *, timeout_seconds: int | None = None,
              proxy: str | None = None, real_page: bool = True) -> str:
        if not url or not sitekey:
            raise CloakSolverError("url and sitekey are required")
        timeout = max(10, int(timeout_seconds or self.timeout_seconds))
        target = url if url.endswith("/") else url + "/"
        if "redirect_uri=" not in target:
            from urllib.parse import quote

            sep = "&" if "?" in target else "?"
            target = f"{target}{sep}redirect_uri={quote(CONSO_REDIRECT_URI, safe='')}"

        opts = {"sitekey": sitekey}
        page_data = STUB_HTML.replace("__CONSO_OPTS__", json.dumps(opts))
        deadline = time.monotonic() + timeout

        with self._sem:
            browser = self._ensure()
            cfg = _proxy_config(proxy or self.proxy)
            ctx = browser.new_context(proxy=cfg) if cfg else browser.new_context()
            try:
                page = ctx.new_page()
                if real_page:
                    page.goto(target, wait_until="domcontentloaded", timeout=30_000)
                    # The page navigates right after load, which destroys the
                    # execution context mid-evaluate. Let it settle first.
                    time.sleep(2.0)
                    try:
                        page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    except Exception:
                        pass
                    self._render(page, opts)
                else:
                    page.route(target, lambda route: route.fulfill(body=page_data, status=200))
                    page.goto(target, wait_until="domcontentloaded", timeout=25_000)

                while time.monotonic() < deadline:
                    try:
                        token = page.evaluate(
                            "() => { const e=document.querySelector('[name=cf-turnstile-response]'); return e?e.value:'' }"
                        )
                        if token and len(token) > 50:
                            return token
                    except Exception:
                        pass
                    time.sleep(0.3)
                raise CloakSolverError(
                    f"cloakbrowser Turnstile solve timed out after {timeout}s"
                )
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass

    @staticmethod
    def _render(page, opts: dict) -> None:
        """Inject turnstile.render(); retry if a navigation kills the context."""
        # The widget container must exist before render() is called, otherwise
        # Turnstile throws and the promise never settles.
        script = """(opts) => new Promise((resolve) => {
            const slot = (() => {
                let el = document.querySelector('#cf-slot');
                if (!el) { el = document.createElement('div'); el.id = 'cf-slot';
                            document.body.prepend(el); }
                return el;
            })();
            const go = () => { try { window.turnstile.render(slot, opts); } catch (e) {} resolve(); };
            if (window.turnstile) { go(); return; }
            const s = document.createElement('script');
            s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=__cb2";
            window.__cb2 = go;
            document.head.appendChild(s);
            setTimeout(go, 8000);
        })"""
        for attempt in range(3):
            try:
                page.evaluate(script, opts)
                return
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.5)

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

    def __enter__(self) -> "CloakTurnstileSolver":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
