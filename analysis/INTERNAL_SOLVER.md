# Internal solver (Camoufox) — status & findings

Applied GROK-CLI's in-process Turnstile solver to Conso.

## What was added

- `src/conso/internal_solver.py` — `InternalTurnstileSolver` (Camoufox
  in-process): persistent browser, dedicated asyncio loop, `Semaphore(4)`,
  context per solve, per-solve proxy, cooldown, real-page solve.
- `captcha.py` — `CAPTCHA_PROVIDER=internal` → `InternalSolverAdapter`.
- `pip install 'camoufox[geoip]'` + browser fetched.

## Environment fix required

Camoufox's bundled browser and Playwright must match. The installed Playwright
**1.62** requires browser **>= beta.30**, but the cached browser was **beta.28**
(from GROK-CLI), so every launch re-downloaded and hung. Fixed by pinning
**`playwright==1.60`**, which works with beta.28:

```
camoufox 0.5.6 + playwright 1.60 + browser 152.0.4-beta.28  -> launch OK
```

## Result on Conso: does NOT solve (browser choice)

Verified with both backends on the current `verify-human` page:

| Backend | Browser | Result |
|---|---|---|
| internal (Camoufox) | Firefox | widget never renders; page stuck "Verifying — CONSO" |
| sidecar (`:8877`) | Chromium (CloakBrowser) | also timed out |

Console on Camoufox shows the cause:

```
Content-Security-Policy: The page's settings blocked an inline script
(script-src-elem) ...
```

The `verify-human` page now server-renders a **"Verifying — CONSO"**
interstitial and loads `turnstile/v0/api.js` via Next.js `<Script>`; the widget
is rendered client-side by `window.turnstile.render(...)`. On Camoufox the CSP
blocks that inline script, so `.cf-turnstile` never appears and no token is
produced. The page also ends in "Problem loading page" after ~12s.

The page's whitelist and render logic are unchanged (`redirect_uri` match is
correct; `turnstile.render` with the sitekey). So this is a **browser-
compatibility** issue with the current page, not a config error.

## Conclusion

The internal Camoufox solver works: stub page + explicit `turnstile.render()`
from the api.js onload callback produces a token Conso accepts.

### Throughput tuning (SOLVER_MAX_CONCURRENT)

Measured with `register N --earn`, per-account proxy:

| SOLVER_MAX_CONCURRENT | batch | result | wall |
|---|---|---|---|
| 1 | 3 | 3/3 | 48s |
| 2 | 3 | 3/3 | 38s |
| 4 | 5 | 5/5 | 64s |
| **8** | **8** | **8/8** | **88s** |
| 16 | 10 | 9/10 (Page.goto timeout) | ~235s |

**Sweet spot: 8.** At 16 concurrent solves the single Camoufox browser
overloads (page navigation times out) and one solve fails, plus the run is
slower overall. Default is `SOLVER_MAX_CONCURRENT=8`.

Each account is pinned to its own proxy for the HTTP calls, so signups do not
share an IP — this is what keeps `signup_velocity_exceeded` away across a batch.
