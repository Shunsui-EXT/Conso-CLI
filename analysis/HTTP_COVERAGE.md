# Full-HTTP coverage audit (per feature)

Decision: keep the **local captcha solver** (CloakBrowser sidecar). Question
answered here: apart from the solver, is everything else pure HTTP?

Method: per-module import scan, external-endpoint inventory, and live probes of
the X OAuth flow.

## Verdict

**Yes — everything except the captcha solve is pure HTTP.** Two caveats, both
narrow:

1. **Captcha solve** (by decision): local CloakBrowser sidecar — the only
   browser in the project.
2. **X account linking**: not part of register/farm/earn, but if used it needs a
   logged-in X session (a second non-HTTP point). Not required by the current
   pipeline.

## Per-module transport

| Module | Third-party libs | Transport |
|---|---|---|
| `transport.py` | `curl_cffi` | HTTP (TLS/JA3 fingerprint) |
| `client.py` | — | HTTP (Supabase/PostgREST/conso.xyz) |
| `verifiers.py` | `imaplib` (stdlib socket) | HTTP (temp.tf/mail.tm/ncaori) or IMAP |
| `captcha.py` | — (HTTP client) | HTTP to solver/managed API |
| `economy.py` | `tiktoken` | local compute |
| `identity.py` | `secrets`, `random` | local compute |
| `session.py` | `base64`, `json` | local + HTTP refresh |
| `storage.py` | `csv`, `json` | local files |
| `concurrency.py` | `threading` | local |
| `pipeline.py` | `concurrent.futures` | orchestration |
| `scheduler.py` | `threading` | orchestration |
| `earnings.py` / `limits.py` | — | HTTP (RPC) |
| `cli.py` | `typer` | local |

No browser library appears in `src/` (verified with a project-wide grep). The
only non-`curl_cffi` third-party libs are `tiktoken` (tokenizer), `typer` (CLI),
`dotenv` (config), `rich` (output) — none open a browser.

## Per-feature transport

| Feature | Path | HTTP? |
|---|---|---|
| `register` | Supabase auth + OTP + create_consouser + referral + onboarding | ✅ (except captcha) |
| `farm` | `append_prompt` RPC | ✅ |
| `earn` | mission/code RPCs | ✅ |
| `session` | refresh/login | ✅ (except captcha on password login) |
| `recover` | refresh → password → email OTP | ✅ (except captcha) |
| `loop` | orchestrates the above | ✅ (except captcha) |
| `status` | consousers row read | ✅ |
| `export` / `verify` / `doctor` | local / proxy check | ✅ |
| **X link** | `conso.xyz/api/*/x/start` → `x.com/i/oauth2/authorize` | ⚠️ needs X login |

## External endpoints (core)

```
jzxlayjrsdbyzykuiqns.supabase.co   auth + RPC        HTTP
www.conso.xyz                      API + X proxy     HTTP
temp.tf / api.mail.tm / ncaori     email OTP         HTTP
api.capsolver.com / 2captcha.com   managed captcha   HTTP
api.ipify.org                      proxy health      HTTP
127.0.0.1:8877 (local)             captcha sidecar   browser (by decision)
```

## The one remaining non-HTTP point: X linking

Live probe of the authorize URL (no X session):

```
GET https://x.com/i/oauth2/authorize?...  -> 200
  page text: "To use this App you have to be logged in to X. Log in"
```

So `x_oauth_start` (returns the authorize URL) is HTTP, but completing it
requires an authenticated X session (login + consent). That is inherently
interactive and cannot be a pure-HTTP server-side step.

**Impact: none on the current pipeline.** Register, farm, earn, session,
recover, and loop never call X linking. The tweet/article missions are claimed
via `claim_daily_mission`/`claim_bonus_mission` and are **not server-validated**,
so they do not need a real X link either.

## Bottom line

- Everything the pipeline actually uses is **pure HTTP** except the captcha
  solve, which by decision is the local solver.
- The only other non-HTTP point is X account linking, which the pipeline does
  not use.
- If a future feature needs a verified X link, that one step would need either
  a logged-in X session or a browser; nothing else would.
