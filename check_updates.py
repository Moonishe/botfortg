"""Check if there are pending Telegram updates for the bot."""

import urllib.request
import json
import sys

TOKEN = "8821137770:AAHeYFTC9l2AXSCJhIzcVL7kEJvnbYMW97g"
url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?offset=-1&limit=10&timeout=1"

try:
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read())
        print("ok:", data.get("ok"))
        results = data.get("result", [])
        print("count:", len(results))
        for u in results:
            msg = u.get("message", {})
            text = msg.get("text", "N/A")
            from_id = msg.get("from", {}).get("id", "?")
            date = msg.get("date", "?")
            print(
                f"  update_id={u['update_id']}, text={text}, from={from_id}, date={date}"
            )
except Exception as e:
    print(f"Error: {e}")

# Also check getMe
url2 = f"https://api.telegram.org/bot{TOKEN}/getMe"
try:
    with urllib.request.urlopen(url2, timeout=10) as r:
        data = json.loads(r.read())
        result = data.get("result", {})
        print(
            f"\nBot info: @{result.get('username')}, id={result.get('id')}, can_join_groups={result.get('can_join_groups')}"
        )
except Exception as e:
    print(f"getMe error: {e}")

# Check webhook
url3 = f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo"
try:
    with urllib.request.urlopen(url3, timeout=10) as r:
        data = json.loads(r.read())
        result = data.get("result", {})
        print(f"\nWebhook URL: {result.get('url', 'NONE')}")
        print(f"Pending updates: {result.get('pending_update_count', 0)}")
        print(f"Last error: {result.get('last_error_message', 'none')}")
        print(f"Max connections: {result.get('max_connections', '?')}")
except Exception as e:
    print(f"getWebhookInfo error: {e}")
