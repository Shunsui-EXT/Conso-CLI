# Browser-free / full-HTTP analysis

Two questions:

1. Can the full pipeline run in pure HTTP (no browser)?
2. Can the **local** solver be made pure HTTP too?

Evidence base: `src/conso/` source, `requirements.txt`, and the primary artifact
`https://challenges.cloudflare.com/turnstile/v0/b/<id>/api.js` (86,732 bytes,
fetched and inspected).

## 1. Full pipeline in pure HTTP — YES (one decision point)

Every layer of `src/conso/` is already HTTP-only. Verified:

| Layer | Mechanism | Browser |
|---|---|---|
| transport | `curl_cffi` (JA3/TLS fingerprint) | no |
| auth | Supabase GoTrue REST | no |
| data | PostgREST RPC | no |
| email | temp.tf / mail.tm HTTP API | no |
| captcha (managed) | Capsolver / 2Captcha REST | no |
| captcha (sidecar) | `vendor/captcha-solver` CloakBrowser | **yes** |

`grep -rniE "playwright|cloakbrowser|selenium|chromium" src/` → **NONE**.
`requirements.txt` = curl_cffi, httpx, tiktoken, typer, dotenv — no browser.
The `analysis/*.py` e2e scripts are also pure HTTP (stdlib `urllib`).

So the pipeline runs **fully browser-free** the moment the captcha layer is a
managed API:

```bash
CAPTCHA_PROVIDER=capsolver   # or 2captcha
python main.py doctor        # -> browser_free=True
```

The only place a browser ever appears is the optional local Turnstile sidecar.

## 2. Local solver as pure HTTP — NOT practical

The local solver (`vendor/captcha-solver/turnstile/solve.py`) is 100% browser:
it launches CloakBrowser, route-intercepts, clicks the widget, and reads
`[name=cf-turnstile-response]`. There is no non-browser path in it.

Can that be reimplemented in pure HTTP? From `api.js`:

- The token is issued by a **`POST` to
  `{origin}/cdn-cgi/challenge-platform/{h/…}c/{o}`** with body
  `{"secondaryToken": <p>, "sitekey": <key>}` (found verbatim in api.js).
- But `secondaryToken` is produced at runtime by the obfuscated challenge
  script running inside a **cross-origin iframe** (44 `iframe` references, 15
  `challenge`, `postMessage` hand-offs) that reads browser signals
  (`navigator`, DOM, iframe origin).
- No raw PoW/wasm shortcut is exposed (`wasm`/`proof`/`pow` markers = 0); the
  work is JS execution + environment fingerprinting, not a hash puzzle you can
  compute offline.

Consequence: a "pure HTTP" local solver would have to **reimplement the
challenge VM and a DOM/iframe environment** (jsdom-class emulation) to
manufacture a valid `secondaryToken`. That is not HTTP-only — it is "move the
JS engine out of a browser into a JS runtime + DOM shim", which is:

- still JavaScript execution (not pure HTTP),
- far more fragile than a real browser (any fingerprint mismatch → no token),
- a large, perpetual reverse-engineering effort against an obfuscated,
  frequently-rotated script.

Turnstile tokens are also **single-use and short-lived (~300s)**, so they cannot
be cached or farmed — every solve needs a fresh challenge round-trip.

### Realistic options

| Option | Browser-free? | Notes |
|---|---|---|
| Managed API (Capsolver/2Captcha) | **yes** | costs per solve; IP-agnostic |
| Local CloakBrowser sidecar | no | free; can hit Cloudflare rate-flags |
| JS runtime + DOM shim (custom) | no | not pure HTTP; fragile; big effort |
| Token caching / reuse | n/a | impossible (single-use, 300s) |

## Bottom line

- **Full pipeline, pure HTTP:** yes — already true except the captcha choice;
  use a managed API and it is 100% HTTP.
- **Local solver, pure HTTP:** not realistic. Turnstile requires JS execution
  in a browser-like environment; "HTTP-only" for a local solver is an illusion.
  The browser-free path is the managed API, not a rewritten local solver.
