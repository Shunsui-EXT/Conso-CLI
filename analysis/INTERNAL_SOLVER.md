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

The internal Camoufox solver is **installed and functional at the browser
level** (launch + navigation verified), but Camoufox (Firefox) does not render
this specific page's Turnstile widget. The Chromium-based path (the existing
CloakBrowser sidecar) is the one that historically produced accepted tokens;
keep using it. Revisit Camoufox if the page's CSP/rendering changes, or run
Camoufox with a patched CSP if that becomes necessary.
