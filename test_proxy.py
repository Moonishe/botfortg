"""Test Telegram API connectivity through FlClash proxy."""

import urllib.request
import json
import ssl

TOKEN = "8821137770:AAHeYFTC9l2AXSCJhIzcVL7kEJvnbYMW97g"

ctx = ssl.create_default_context()

# Test 1: Direct connection
print("=== Test 1: Direct connection ===")
try:
    url = f"https://api.telegram.org/bot{TOKEN}/getMe"
    with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
        data = json.loads(r.read())
        result = data.get("result", {})
        print(f"SUCCESS: @{result.get('username')}, id={result.get('id')}")
except Exception as e:
    print(f"FAILED: {e}")

# Test 2: Through HTTP proxy (FlClash port 54106)
print("\n=== Test 2: HTTP proxy 127.0.0.1:54106 ===")
try:
    proxy_handler = urllib.request.ProxyHandler(
        {
            "https": "http://127.0.0.1:54106",
            "http": "http://127.0.0.1:54106",
        }
    )
    opener = urllib.request.build_opener(proxy_handler)
    url = f"https://api.telegram.org/bot{TOKEN}/getMe"
    with opener.open(url, timeout=10, context=ctx) as r:
        data = json.loads(r.read())
        result = data.get("result", {})
        print(f"SUCCESS: @{result.get('username')}, id={result.get('id')}")
except Exception as e:
    print(f"FAILED: {e}")

# Test 3: Through SOCKS5 proxy
print("\n=== Test 3: SOCKS5 proxy 127.0.0.1:7891 ===")
try:
    import socks
    import socket

    socks.set_default_proxy(socks.SOCKS5, "127.0.0.1", 7891)
    socket.socket = socks.socksocket
    url = f"https://api.telegram.org/bot{TOKEN}/getMe"
    with urllib.request.urlopen(url, timeout=10, context=ctx) as r:
        data = json.loads(r.read())
        result = data.get("result", {})
        print(f"SUCCESS: @{result.get('username')}, id={result.get('id')}")
except ImportError:
    print("SKIP: PySocks not installed")
except Exception as e:
    print(f"FAILED: {e}")
