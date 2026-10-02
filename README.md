# Conso Automation Pipeline

Reverse-engineered automation pipeline for the **Conso: AI Usage Tracker**
Chrome extension (v0.1.4.0). Talks directly to the Conso backend (Supabase +
`conso.xyz` API) — API-first, no browser required for turn submission.

See `analysis/RE_REPORT.md` for the full reverse-engineering write-up.

## Layout

```
main.py                     entry point (python main.py <cmd>)
src/conso/
  constants.py              every extracted constant (endpoints, model tables, prices)
  economy.py                faithful port of the client-side zap/USD/quality math
  transport.py              curl_cffi fingerprinting + proxy pool with quarantine
  client.py                 Supabase auth + RPC + conso.xyz X-API client
  captcha.py                pluggable Turnstile solvers (service/capsolver/2captcha/manual)
  verifiers.py              pluggable email adapters (temptf/mailtm/ncaori/imap/tempmail)
  identity.py               email/password/consoname generation
  session.py                session manager (cached reuse / refresh+persist / password)
  storage.py                JSON+CSV account store, atomic writes, checkpoints
  concurrency.py            adaptive concurrency engine + jittered pacer
  pipeline.py               registration + turn-farming orchestration
  earnings.py               missions, codes, account-row + summary
  cli.py                    typer CLI (8 subcommands)
  config.py                 env/.env settings
extension_original/         downloaded CRX + unpacked extension
analysis/                   RE report, schema, audit, e2e probes
scripts/                    setup_solver.sh / start_solver.sh
data/                       accounts.json / accounts.csv / state.json (gitignored)
```

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill EMAIL_DOMAIN etc.
```

## Usage

```bash
export PYTHONPATH=src

# Pre-flight: transport, backend reachability, economy sanity
python main.py test

# Provision accounts (resumable; dry-run generates identities only)
python main.py register 10 --dry-run
python main.py register 10 --referral <CODE>
python main.py register 10 --earn            # register THEN immediately earn zaps

# Submit synthetic turns to earn zaps
python main.py farm --turns 20 --dry-run
python main.py farm --turns 20 --email user@domain

# Export / proxy health
python main.py export --fmt csv
python main.py verify
```

## Configuration

All via `.env` (see `.env.example`). Key values:

| Var | Meaning |
|---|---|
| `EMAIL_DOMAIN` | Catch-all / disposable domain for account emails (**required**) |
| `PROXY_URLS` | Comma-separated HTTP/SOCKS5 pool (empty = direct) |
| `VERIFIER` | `none` \| `imap` \| `tempmail` |
| `CONCURRENCY` / `MAX_CONCURRENCY` | Adaptive concurrency bounds |
| `MIN_DELAY_SECONDS` / `MAX_DELAY_SECONDS` | Jittered pacing between requests |
| `IMPERSONATE` | curl_cffi TLS profile (`chrome`, `chrome124`, …) |

## Status

- **Register does NOT earn by default.** `register` provisions the account
  (signup → OTP → create_consouser → referral → onboarding) and stops at
  `total_zaps = 0`. Earning is a separate step (`farm`, `earn`, `loop`), or pass
  `register --earn` to run the earn cycle immediately after each account.
- **Verified end-to-end (real zaps credited):**
  `temp.tf inbox -> Turnstile solve -> signup -> email OTP -> verify ->
   create_consouser -> set consoname (onboarding) -> append_prompt (N turns) ->
   zaps credited + leaderboard rank improves`.
  Credited zaps equal the client-computed values exactly (server does not
  clamp `p_base_zaps`/`p_spend_usd`).
- **Turnstile:** enforced on all Supabase auth endpoints; solved via the local
  sidecar; submitted in the `gotrue_meta_security` envelope. Stub tokens are
  rejected — only the real-page solve works.
- **Email:** temp.tf provides real `gmail.com`/`outlook.com`/`hotmail.com`
  plus-aliases that pass Conso's allowlist (mail.tm / ncaori domains are
  blocked). Confirmation is a **6-digit OTP**, not a link.
- **Onboarding is mandatory:** `append_prompt` before `set_consoname` bans the
  account (`account_banned`). The pipeline sets the consoname first.
- **Open:** rate limits at scale; whether repeated plus-aliases on the same
  underlying mailbox get flagged; per-account daily caps.

## Daily loop

Run the full earn cycle (missions + turns) on a schedule, resumable across
restarts:

```bash
python main.py loop --once                 # one cycle now, exit
python main.py loop --interval-hours 24    # run daily forever (Ctrl-C to stop)
python main.py loop --turns 10 --daily-cap 25
python main.py loop --no-missions          # turns only
```

The loop records each run in `state.json` (`daily_loop.<date>.<email>`) and
skips accounts already done that day, so overlaps/restarts never double-submit.
It also stops an account at the first zero-credit turn (the daily cap).

## Daily limits

`append_prompt` credits only ~**10 turns per account per day**; past that the
server returns `200 0` (soft flag) and bans on the next turn. The pipeline stops
at the first zero-credit turn automatically. Check state with:

```bash
python main.py status        # total/daily zaps, streak, boost, banned flag
```

`limits.py` exposes `get_daily_status()` (reads `daily_zaps_earned` /
`daily_zaps_date`, resets on date rollover) and `classify_limit()`.

## Earning surfaces

Beyond turn farming, the pipeline claims every mission/code path:

| Surface | RPC | Reward |
|---|---|---|
| Daily check-in | `claim_daily_mission(daily-checkin-v1)` | +2 |
| Tweet mission | `claim_daily_mission(tweet-about-conso-v1)` | +3 |
| Article mission | `claim_bonus_mission(article-about-conso-v1)` | +15 |
| Referral code | `redeem_referral_code` | varies |
| Access code | `redeem_access_code` | varies |
| Turn farming | `append_prompt` | per-turn formula |

Claims are **idempotent per day** — a repeat returns `rate_limited`, treated as
already-claimed. `get_todays_mission_claims` lists what's done, so runs skip it.

```bash
python main.py earn --list           # show available missions
python main.py earn                  # claim all (all accounts)
python main.py earn --referral CODE  # also redeem a referral code
python main.py earn --access CODE    # also redeem an access code
python main.py farm --turns 10       # claims missions THEN farms (--no-earn to skip)
```

`get_lifetime_platform_stats` returns per-platform aggregates
(`credited_zaps`, `spend_usd`, `prompt_count`, …) for reporting.

## Referral

Every new account redeems a default referral code automatically. Set it once:

```bash
# .env
DEFAULT_REFERRAL_CODE=CONSO-GG53G
```

Order matters: the extension shows the referral screen right after
`create_consouser` and **before** picking a consoname, so the pipeline redeems
the code before onboarding (`create_consouser -> redeem_referral_code ->
set_consoname`). Per-run override: `python main.py register 5 --referral CODE`.

## Solver proxy pool

The captcha sidecar accepts a per-request `proxy` field, so the solver can run
each solve through a rotating proxy (clean IP per solve):

```bash
# .env
SOLVER_PROXY_FILE=data/proxies.txt    # one proxy per line: http://user:pass@host:port
```

```bash
python main.py proxies               # health-check the list
python main.py proxies --limit 20    # test the first 20
```

`SolverServiceSolver` round-robins the list and sends `proxy` in each `/solve`
body. **Any** proxy attempt that fails (no token, timeout, 500, socket drop)
falls back to a **direct** solve for that attempt, so a proxy that cannot pass
the Turnstile challenge never blocks the pipeline. Lines are stripped of CRLF —
a trailing `\r` makes the proxy URL malformed and every request fails with
`CONNECT tunnel failed`.

> Datacenter proxies often **cannot pass Turnstile**: Cloudflare serves a harder
> challenge to hosting IPs (the checkbox is clicked but no token is issued, or
> the socket drops with `net::ERR_SOCKET_NOT_CONNECTED`). Verified: a fresh
> solver solves directly in ~10s but times out through all tested proxies. The
> direct fallback keeps solves working; use **residential** proxies if you need
> the solve itself to run on a proxy IP.

## Browser-free mode

The core pipeline (`src/conso/`) is **already pure HTTP** — no Playwright,
CloakBrowser, Selenium, or any browser. Every layer talks HTTP directly:

| Layer | Mechanism |
|---|---|
| Transport | `curl_cffi` (TLS/JA3 fingerprint impersonation) |
| Auth | Supabase GoTrue REST |
| Data | PostgREST RPC |
| Email | temp.tf / mail.tm HTTP API |
| Captcha | Capsolver / 2Captcha **managed API** (pure HTTP) |

The only browser anywhere is the optional `service` captcha sidecar
(`vendor/captcha-solver`, CloakBrowser). To run **fully browser-free**, point
the captcha layer at a managed API instead:

```bash
# .env
CAPTCHA_PROVIDER=capsolver        # or 2captcha
CAPSOLVER_API_KEY=...             # from capsolver.com
```

Verify:

```bash
python main.py doctor
```

`doctor` reports which browser libs are present, confirms `src/` imports none,
and flags whether the configured captcha provider is browser-free.

> Trade-off: the `service` sidecar is free but runs a local browser and can hit
> Cloudflare rate-flags. A managed API is browser-free and IP-agnostic but
> costs per solve. Everything else is identical.

## Session recovery (reuse the signup address)

**Yes — the temp.tf address can be reused later for OTP.** Verified live: an
alias keeps receiving mail as long as the underlying provider account exists
(temp.tf FAQ), and a fresh OTP delivered to a stored address verified back into
a session. So an account is never locked out as long as its address is saved.

Recovery order (used automatically when a session breaks):

```bash
python main.py recover            # refresh -> password (Turnstile) -> email OTP
```

1. **refresh** — reuse the rotated refresh token (no captcha).
2. **password** — `sign_in_password` (needs a Turnstile solve).
3. **email OTP** — `sign_in_otp` to the stored address, read the code from the
   inbox, `verify_otp`. This is the path that proves the address is reusable.

`farm` and `loop` auto-recover when `ensure_session` fails, so a broken session
never needs a manual re-register.

## Sessions (no re-login)

Login requires solving Turnstile, so the pipeline persists and reuses sessions:

- Registration stores `access_token`, `refresh_token`, and `expires_at`.
- `SessionManager.ensure_session()` reuses the cached access token while valid,
  otherwise **refreshes and persists the rotated refresh token** (Supabase
  rotates the refresh token on every use), and only falls back to password
  login (Turnstile) if the refresh chain breaks.
- Farm resolves sessions through `SessionManager`, so `farm` runs with
  `session=cached` / `session=refreshed` — **no Turnstile, no re-login**.

```bash
python main.py session            # show + refresh all stored sessions
python main.py session --force    # force a refresh now
```

Verified: repeated refreshes rotate the token each time and the chain stays
valid; `farm` reuses the cached session with zero re-authentication.

## End-to-end flow

```
register:  temp.tf inbox (real gmail/outlook alias)      verifiers.py
           -> [Turnstile real-page solve @ :8877]        captcha.py
           -> POST /auth/v1/signup + gotrue_meta_security captcha_token
           -> read 6-digit OTP from inbox
           -> POST /auth/v1/verify {type:email,token}     client.py
           -> RPC create_consouser
           -> PATCH consousers {consoname}   (onboarding) client.py
           -> store                                       storage.py
farm:      refresh/login
           -> economy.account_turn()                      economy.py
           -> RPC append_prompt {p_entry,p_base_zaps,p_spend_usd}
           -> credited_zaps (== submitted zaps)
```

## Required environment

```bash
VERIFIER=temptf
TEMPTF_PROVIDER=gmail            # gmail | outlook | hotmail
CAPTCHA_PROVIDER=service
SOLVER_URL=http://127.0.0.1:8877
# start the sidecar first:
bash scripts/setup_solver.sh     # one-time (clones + installs)
bash scripts/start_solver.sh     # run :8877
```

Then:

```bash
python main.py register 1        # real account, fully provisioned + onboarded
python main.py farm --turns 10   # submit turns, earn zaps
```

## Captcha solver sidecar

The vendored `waguriagentic/captcha-solver` (FastAPI, CloakBrowser) runs locally:

```bash
bash scripts/start_solver.sh          # headed under Xvfb, :8877
curl http://127.0.0.1:8877/health
CAPTCHA_PROVIDER=service python main.py solve   # sanity check
```

The Conso solve is stochastic (~1/2-5); the pipeline retries. `SolverServiceSolver`
defaults to `real_page` and auto-appends a whitelisted `redirect_uri`.
