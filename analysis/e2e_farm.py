#!/usr/bin/env python3
"""Submit synthetic turns to the real account and observe zap crediting."""
import json, sys, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone

sys.path.insert(0, "/home/elzanom/Documents/WEB3/CONSO/src")
from conso import economy  # noqa: E402

KEY = "sb_publishable_clAiRg6ffCznEAtg_bn19Q_yY0W5Hyd"
SB = "https://jzxlayjrsdbyzykuiqns.supabase.co"


def http(url, method="GET", body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def main():
    acct = json.load(open("/tmp/active_acct.json"))
    at = acct["access_token"]
    auth = {"apikey": KEY, "Authorization": f"Bearer {at}"}

    specs = [
        ("claude", "claude-opus-4-8", False),
        ("chatgpt", "gpt-5-6", False),
        ("perplexity", "pplx_asi_sonnet", True),
    ]
    prompt = ("Write a production-grade Python module that parses a JSON config, validates "
              "every field against a schema, applies defaults, and returns a typed dataclass.\n"
              "Output format: markdown table and code block, with error handling.")
    response = "Below is a complete implementation.\n```python\ndef load(p): ...\n```\n" * 3

    for platform, model, attach in specs:
        acct_turn = economy.account_turn(
            platform=platform, model=model, prompt_text=prompt, response_text=response,
            has_non_image_attachment=attach,
        )
        entry = economy.build_entry(acct_turn, timestamp=datetime.now(timezone.utc).isoformat())
        st, body = http(f"{SB}/rest/v1/rpc/append_prompt", "POST",
                        {"p_entry": entry, "p_base_zaps": acct_turn.zaps,
                         "p_spend_usd": acct_turn.spend_usd}, headers=auth)
        print(f"[{platform}/{model}] zaps={acct_turn.zaps} spend={acct_turn.spend_usd:.6f}")
        print(f"  -> {st} {body[:300]}")

    st, body = http(f"{SB}/rest/v1/rpc/get_my_leaderboard_rank", "POST", {}, headers=auth)
    print(f"rank after: {st} {body[:120]}")


if __name__ == "__main__":
    main()
