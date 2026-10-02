# Reference study — fast earning constraints

Studied a mature sibling implementation at
`/home/elzanom/work/airdrop/WEB3/conso` (same reverse-engineered API surface,
production-hardened). Three findings that make earning faster and safer were
missing from our pipeline; all are now integrated.

## 1. Per-credit ceiling (the big one)

The server multiplies the submitted `p_base_zaps` by the account's
`boost_factor`, then **credits 0 when the product exceeds a per-credit
ceiling**. Overshooting does not just waste the turn — it is the soft flag that
precedes a ban.

```
credited = base_zaps_sent * boost_factor   (rejected -> 0 when > ceiling)
ceiling ≈ 3.2
```

Verified live on our accounts: `boost_factor = 1.05`, `daily_zaps_earned`
tracked, `is_banned` flag present.

**Fix:** size each turn so `base_zaps_sent * boost_factor ≈ target ≤ ceiling`.
`economy.send_zaps_for_target(target, boost)` returns
`min(target, target/boost, ceiling)`. With boost 1.5 and target 3.0 it sends
2.0 (server ×1.5 = 3.0).

## 2. Daily budget ≈ 21 zaps (hard limit ~30)

The reference caps the daily budget at **21** so a session stops cleanly
("daily cap reached") instead of tripping the server's ~30 limit and risking a
ban. Our old code only stopped on the zero-credit soft flag.

**Fix:** `farm_turns_for_account` reads `daily_zaps_earned`, computes
`remaining = DAILY_ZAP_CAP - daily_used`, spreads it across the turns, and stops
when filled.

## 3. Locked (model, platform) set

Only four pairs earn at the top of the range:

| platform | model |
|---|---|
| claude | `claude-fable-5` (1.4) |
| perplexity | `pplx_asi_fable_5` (1.4) |
| chatgpt | `gpt-5-6-thinking` (0.7) |
| gemini | `gemini-3.1-pro` (0.7) |

Anything else drops to the platform floor (0.1 on gemini). Our old
`make_synthetic_turn` picked random models including weak ones (`auto`,
`gemini-3-flash`).

**Fix:** `TOP_MODELS` in `pipeline.py`; `make_synthetic_turn` now draws from it.

## Also adopted

- **Proactive token refresh** before `exp` (we already rotate+persist; the
  reference refreshes at a 90 s leeway — ours refreshes on demand).
- **Even platform spread** — fill all four platforms toward the per-platform
  target instead of draining one.
- **Pacing** — 60–180 s between turns per account (reference default); ours is
  shorter. Kept configurable via `MIN_DELAY_SECONDS`/`MAX_DELAY_SECONDS`.

## Constants added (`constants.py`)

```python
CREDIT_CEILING = 3.2     # server rejects base_zaps*boost above this
DAILY_ZAP_CAP  = 21.0    # practical daily budget (hard limit ~30)
```
