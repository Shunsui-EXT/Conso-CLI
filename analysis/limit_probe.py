#!/usr/bin/env python3
"""Characterize Conso's rate/daily limits on append_prompt."""
import sys, json, time
from datetime import datetime, timezone
sys.path.insert(0, "/home/elzanom/Documents/WEB3/CONSO/src")
from conso.config import Settings
from conso.client import ConsoClient, ConsoAPIError
from conso.session import SessionManager
from conso.storage import Store
from conso import economy
from conso.limits import get_daily_status, classify_limit

s = Settings.from_env(); store = Store(s.data_dir)
rec = store.all()[0]
c = ConsoClient(s); c.session = SessionManager(s, store).ensure_session(rec).session
print("account:", rec.email)
st0 = get_daily_status(c)
print(f"start: total={st0.total_zaps} daily={st0.daily_zaps_earned} date={st0.daily_zaps_date} stale={st0.is_stale}")

prompt = ("Write a production-grade Python module that parses a JSON config, validates "
          "every field against a schema, applies defaults, and returns a typed dataclass.\n"
          "Output format: markdown table and code block, with error handling.")
ok = 0; rejected = 0; first_err = None
for i in range(40):
    turn = economy.account_turn(platform="claude", model="claude-opus-4-8",
        prompt_text=prompt + f"\nCase {i} {time.time_ns()}",
        response_text="Complete implementation.\n```python\n...\n```\n" * 3)
    entry = economy.build_entry(turn, timestamp=economy.js_isoformat(datetime.now(timezone.utc)))
    try:
        r = c.append_prompt(entry, turn.zaps, turn.spend_usd)
        ok += 1
        if i < 3 or i % 10 == 0:
            print(f"  #{i+1} ok credited={r}")
    except ConsoAPIError as e:
        rejected += 1
        cls = classify_limit(str(e))
        msg = str(e).split('message":')[-1][:120]
        if first_err is None:
            first_err = (i+1, msg, cls)
            print(f"  #{i+1} REJECTED class={cls} {msg}")
        if cls == "ban":
            break
    # no pacing — deliberately hammer to find the limit
st1 = get_daily_status(c)
print(f"end: ok={ok} rejected={rejected} first_reject={first_err}")
print(f"final: total={st1.total_zaps} daily={st1.daily_zaps_earned} date={st1.daily_zaps_date} banned={st1.is_banned}")
c.close()
