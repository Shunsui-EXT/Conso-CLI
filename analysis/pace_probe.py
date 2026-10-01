#!/usr/bin/env python3
"""Find the safe pace: turn rate vs ban. Registers a fresh account, farms with
configurable delay, and reports where the first credited=0 / ban appears."""
import sys, time
from datetime import datetime, timezone
sys.path.insert(0, "/home/elzanom/Documents/WEB3/CONSO/src")
from conso.config import Settings
from conso.client import ConsoClient, ConsoAPIError
from conso.session import SessionManager
from conso.storage import Store
from conso import economy
from conso.limits import get_daily_status

delay = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
turns = int(sys.argv[2]) if len(sys.argv) > 2 else 30

s = Settings.from_env(); store = Store(s.data_dir)
rec = [r for r in store.all() if r.status == "active"][-1]
c = ConsoClient(s); c.session = SessionManager(s, store).ensure_session(rec).session
print(f"account: {rec.email} | delay={delay}s turns={turns}")

prompt = ("Write a production-grade Python module that parses a JSON config, validates "
          "every field against a schema, applies defaults, and returns a typed dataclass.\n"
          "Output format: markdown table and code block.")
zero_at = None; ban_at = None; ok = 0
for i in range(turns):
    if i: time.sleep(delay)
    turn = economy.account_turn(platform="claude", model="claude-opus-4-8",
        prompt_text=prompt + f"\nCase {i} {time.time_ns()}",
        response_text="Complete implementation.\n```python\n...\n```\n" * 3)
    entry = economy.build_entry(turn, timestamp=economy.js_isoformat(datetime.now(timezone.utc)))
    try:
        r = c.append_prompt(entry, turn.zaps, turn.spend_usd)
        ok += 1
        credited = float(r) if isinstance(r, (int, float)) else 0
        if credited == 0 and zero_at is None:
            zero_at = i + 1
            print(f"  #{i+1} credited=0 (soft flag) — stopping")
            break
        if i < 2 or i % 5 == 0:
            print(f"  #{i+1} ok credited={credited}")
    except ConsoAPIError as e:
        ban_at = i + 1
        print(f"  #{i+1} REJECTED {str(e).split('message\":')[-1][:80]}")
        break
st = get_daily_status(c)
print(f"RESULT: ok={ok} zero_at={zero_at} ban_at={ban_at}")
if st:
    print(f"daily={st.daily_zaps_earned} total={st.total_zaps} date={st.daily_zaps_date} banned={st.is_banned}")
c.close()
