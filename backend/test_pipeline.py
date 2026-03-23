#!/usr/bin/env python3
"""Quick end-to-end pipeline test."""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"

def post_json(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode())

def get_json(path):
    req = urllib.request.Request(f"{BASE}{path}")
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode())

print("=== 1. Health check ===")
print(json.dumps(get_json("/api/health"), indent=2))

print("\n=== 2. Ingest OSPF down ===")
r = post_json("/api/aiops/logs/ingest", {
    "source_ip": "10.255.0.1",
    "raw_message": "%OSPF-5-ADJCHG: Process 1, Nbr 10.255.0.2 on GigabitEthernet0/1 from FULL to DOWN, Neighbor Down: Dead timer expired",
    "hostname": "R1",
})
print(json.dumps(r, indent=2))

print("\n=== 3. Ingest LINK down ===")
r = post_json("/api/aiops/logs/ingest", {
    "source_ip": "10.255.0.1",
    "raw_message": "%LINK-3-UPDOWN: Interface GigabitEthernet0/1, changed state to down",
    "hostname": "R1",
})
print(json.dumps(r, indent=2))

print("\n=== 4. Ingest noise (should be filtered) ===")
r = post_json("/api/aiops/logs/ingest", {
    "source_ip": "10.255.0.1",
    "raw_message": "Cisco IOS Software, IOSv Software (VIOS-ADVENTERPRISEK9-M), Version 15.9(3)M8",
    "hostname": "R1",
})
print(json.dumps(r, indent=2))

print("\n=== 5. Check incidents ===")
incidents = get_json("/api/aiops/incidents")
print(f"Total incidents: {len(incidents)}")
for inc in incidents[:5]:
    print(f"  {inc.get('incident_no','?')} | {inc.get('title','?')} | status={inc.get('status','?')} | severity={inc.get('severity','?')}")

print("\n=== 6. Check logs ===")
logs = get_json("/api/aiops/logs?limit=10")
print(f"Total logs returned: {len(logs)}")
for log in logs[:5]:
    print(f"  id={log.get('id','?')} | family={log.get('event_family','?')} | state={log.get('event_state','?')} | {log.get('title','?')[:60]}")

print("\n=== 7. Dashboard ===")
dash = get_json("/api/aiops/dashboard")
print(json.dumps(dash, indent=2, default=str)[:500])

print("\nDone!")
