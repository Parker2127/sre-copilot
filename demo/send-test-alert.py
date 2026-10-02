#!/usr/bin/env python3
"""Fire the sample Alertmanager webhook at a local SRE Copilot.

Usage:
    python demo/send-test-alert.py                 # http://localhost:8080/alerts
    python demo/send-test-alert.py http://host:port/alerts
    API_KEY=s3cret python demo/send-test-alert.py  # when webhook auth is enabled

Stdlib only — no extra dependencies.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080/alerts"
SAMPLE = Path(__file__).parent / "sample-alert.json"

payload = SAMPLE.read_bytes()
request = urllib.request.Request(URL, data=payload,
                                 headers={"Content-Type": "application/json"})
if os.environ.get("API_KEY"):
    request.add_header("X-API-Key", os.environ["API_KEY"])

try:
    with urllib.request.urlopen(request, timeout=30) as resp:
        body = json.loads(resp.read().decode())
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code}: {exc.read().decode()[:500]}")
    sys.exit(1)

for result in body.get("results", []):
    d = result["diagnosis"]
    print(f"alert:    {result['alert']} ({result['severity']})")
    print(f"source:   {d.get('source')}")
    print(f"summary:  {d.get('summary')}")
    print("actions:")
    for i, a in enumerate(d.get("suggested_actions", []), 1):
        print(f"  {i}. {a}")
print(f"\nprocessed={body.get('processed')} skipped_resolved={body.get('skipped_resolved')}")
