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
  identity.py               email/password/consoname generation
  verifiers.py              pluggable email-verification adapters (imap/tempmail)
  storage.py                JSON+CSV account store, atomic writes, checkpoints
  concurrency.py            adaptive concurrency engine + jittered pacer
  pipeline.py               registration + turn-farming orchestration
  cli.py                    typer CLI
extension_original/         downloaded CRX + unpacked extension
analysis/                   reverse-engineering report
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
