import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from src.aiops.parser import parse_syslog

samples = [
    ("10.255.0.1", "R1", "%OSPF-5-ADJCHG: Process 1, Nbr 10.255.0.2 on GigabitEthernet0/1 from FULL to DOWN, Neighbor Down: Dead timer expired"),
    ("10.255.0.1", "R1", "%LINK-3-UPDOWN: Interface GigabitEthernet0/1, changed state to down"),
    ("10.255.0.1", "R1", "Cisco IOS Software, IOSv Software"),
    ("10.255.0.1", "R1", "%SYS-5-RESTART: System restarted"),
    ("10.255.0.1", "R1", "%BGP-5-ADJCHANGE: neighbor 10.255.0.3 Down BGP Notification sent"),
    ("10.255.0.1", "R1", "%OSPF-5-ADJCHG: Process 1, Nbr 10.255.0.2 on GigabitEthernet0/1 from DOWN to FULL, Neighbor Up"),
]
for ip, hn, msg in samples:
    r = parse_syslog(ip, hn, msg)
    if r is None:
        print(f"NOISE | {msg[:70]}")
    else:
        print(f"{r['event_family']:12} | {r['event_state']:10} | {r['severity']:10} | {r['correlation_key']}")
