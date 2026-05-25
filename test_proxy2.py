"""Test Telegram API through local proxy on port 1080."""

import urllib.request
import json
import socket

TOKEN = "8821137770:AAHeYFTC9l2AXSCJhIzcVL7kEJvnbYMW97g"

# Test direct
print("=== Direct ===")
try:
    socket.setdefaulttimeout(5)
    s = socket.create_connection(("api.telegram.org", 443), timeout=5)
    print("SUCCESS: can reach api.telegram.org")
    s.close()
except Exception as e:
    print(f"FAILED: {e}")

# Check who owns port 1080
import subprocess

r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True)
for line in r.stdout.split("\n"):
    if ":1080" in line and "LISTENING" in line:
        print(f"\nPort 1080 listener: {line.strip()}")
        pid = line.strip().split()[-1]
        r2 = subprocess.run(
            ["tasklist", "/fi", f"PID eq {pid}"], capture_output=True, text=True
        )
        for l2 in r2.stdout.split("\n"):
            if "exe" in l2.lower() or pid in l2:
                print(f"Process: {l2.strip()}")
