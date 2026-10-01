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

- **Verified end-to-end:** extension extraction, backend endpoints, RPC
  signatures, zap formula, dedup semantics, tokenizer encoding; Turnstile
  bypass (real-page solve -> `gotrue_meta_security.captcha_token` -> signup
  200); email-domain allowlist (gmail/outlook allowed, temp-mail blocked);
  email confirmation requirement.
- **Turnstile:** Conso enforces Cloudflare Turnstile on **all** Supabase auth
  endpoints. Solved via the local `captcha-solver` sidecar
  (`scripts/start_solver.sh`, `:8877`), then submitted in the
  `gotrue_meta_security` envelope. A stub token is rejected; the **real-page**
  solve against `verify-human?redirect_uri=<whitelisted>` is required.
- **Email wall:** signup rejects disposable domains
  (`email_domain_not_allowed`). mail.tm (`uberip.com`) and ncaori are blocked.
  Use a catch-all/alias on an **allowed** provider (gmail/outlook), then confirm
  the address via the verifier.
- **Unverified:** whether the server clamps client-supplied `p_base_zaps` /
  `p_spend_usd`, and server-side rate limits on `append_prompt`.

## End-to-end flow

```
register:  identity
           -> [Turnstile real-page solve @ :8877]        captcha.py
           -> POST /auth/v1/signup + gotrue_meta_security captcha_token
           -> confirm email (verifier)                    verifiers.py
           -> POST /auth/v1/token?grant_type=password
           -> RPC create_consouser
           -> store                                        storage.py
farm:      refresh/login
           -> economy.account_turn()                       economy.py
           -> RPC append_prompt {p_entry,p_base_zaps,p_spend_usd}
           -> credited_zaps
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
