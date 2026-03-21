# Live Lab Topology And Automation Context

Generated from live SSH collection at `2026-03-21T17:13:48+07:00`.

## Collection Summary
- Scope: `9` devices from `backend/inventory/inventory.csv`
- Reachability: `9/9` devices reachable over SSH
- Commands executed: `116` read-only commands
- SSH / CLI result: no collection-time errors were returned
- Raw evidence snapshot saved at `live-lab-device-evidence.json`

## Executive Summary
- The lab is a dual-layer HQ core/distribution design with two branch sites and one management router.
- `OSPF 10` is the HQ and management underlay control plane.
- `EIGRP 100` is the branch overlay control plane over GRE/IPsec tunnels.
- `HQ-CORE-RT01` is the only observed internet edge router. It runs `BGP AS 65000` to external neighbor `100.66.0.2 (AS 64512)` and redistributes BGP into OSPF.
- `HQ-DIST-GW01` and `HQ-DIST-GW02` act as redistribution points between `OSPF 10` and `EIGRP 100`.
- Each branch is dual-homed with a primary and backup tunnel path.
- Each branch access switch is an L2 trunk uplink to its branch router and uses `VLAN 99` for switch management.

## Confirmed Physical And L2 Links
- `LAB-MGMT-BR01 Gi0/1 10.255.10.1` <-> `HQ-CORE-RT01 Gi0/1 10.255.10.2`
- `LAB-MGMT-BR01 Gi0/2 10.255.10.5` <-> `HQ-CORE-RT02 Gi0/1 10.255.10.6`
- `HQ-CORE-RT01 Gi0/0 10.255.10.9` <-> `HQ-CORE-RT02 Gi0/0 10.255.10.10`
- `HQ-CORE-RT01 Gi0/2 10.255.10.13` <-> `HQ-DIST-GW01 Gi0/1 10.255.10.14`
- `HQ-CORE-RT02 Gi0/2 10.255.10.17` <-> `HQ-DIST-GW02 Gi0/1 10.255.10.18`
- `HQ-DIST-GW01 Gi0/0 10.255.10.21` <-> `HQ-DIST-GW02 Gi0/0 10.255.10.22`
- `HQ-CORE-RT01 Gi0/3 100.66.0.1` <-> `ISP-RT Gi0/3 100.66.0.2`
- `BRANCH-A-RTR Gi0/3` <-> `BRANCH-A-Switch Gi0/1` as `802.1Q trunk`, allowed VLANs `10,20,99`
- `BRANCH-B-RTR Gi0/3` <-> `BRANCH-B-Switch Gi0/1` as `802.1Q trunk`, allowed VLANs `10,20,99`

## Confirmed WAN / Overlay Mapping
- `HQ-DIST-GW01 Gi0/2.101 100.64.1.1` <-> `BRANCH-A-RTR Gi0/1 100.64.1.2` -> `Tunnel10 172.16.10.1/30 <-> 172.16.10.2/30` -> primary Branch A path
- `HQ-DIST-GW02 Gi0/2.201 100.65.1.1` <-> `BRANCH-A-RTR Gi0/2 100.65.1.2` -> `Tunnel20 172.16.20.1/30 <-> 172.16.20.2/30` -> backup Branch A path
- `HQ-DIST-GW01 Gi0/2.102 100.64.1.5` <-> `BRANCH-B-RTR Gi0/1 100.64.1.6` -> `Tunnel30 172.16.30.1/30 <-> 172.16.30.2/30` -> primary Branch B path
- `HQ-DIST-GW02 Gi0/2.202 100.65.1.5` <-> `BRANCH-B-RTR Gi0/2 100.65.1.6` -> `Tunnel40 172.16.40.1/30 <-> 172.16.40.2/30` -> backup Branch B path

## Confirmed Logical / Control-Plane Relationships
- OSPF adjacency: `LAB-MGMT-BR01` <-> `HQ-CORE-RT01`
- OSPF adjacency: `LAB-MGMT-BR01` <-> `HQ-CORE-RT02`
- OSPF adjacency: `HQ-CORE-RT01` <-> `HQ-DIST-GW01`
- OSPF adjacency: `HQ-CORE-RT02` <-> `HQ-DIST-GW02`
- OSPF adjacency: `HQ-DIST-GW01` <-> `HQ-DIST-GW02`
- EIGRP adjacency: `HQ-DIST-GW01 Tunnel10` <-> `BRANCH-A-RTR Tunnel10`
- EIGRP adjacency: `HQ-DIST-GW02 Tunnel20` <-> `BRANCH-A-RTR Tunnel20`
- EIGRP adjacency: `HQ-DIST-GW01 Tunnel30` <-> `BRANCH-B-RTR Tunnel30`
- EIGRP adjacency: `HQ-DIST-GW02 Tunnel40` <-> `BRANCH-B-RTR Tunnel40`
- BGP adjacency: `HQ-CORE-RT01 AS65000` <-> `100.66.0.2 AS64512`

## Key Configuration Dependencies For Automation
- `HQ-CORE-RT01` is the only observed BGP edge and redistributes BGP into OSPF. Internet or external route faults will likely surface here first.
- `HQ-CORE-RT02` is OSPF-only in the current evidence. It appears to be a core peer and alternate HQ path, not an active internet edge.
- `HQ-DIST-GW01` and `HQ-DIST-GW02` redistribute OSPF <-> EIGRP. They are the control-plane bridge between HQ underlay and branch overlays.
- Branch default routing is tied to tracked IP SLA on the primary tunnel.
- `BRANCH-A-RTR` uses `track 10` with `ip sla 10`, probing `172.16.10.1` from `Tunnel10`, and installs `0.0.0.0/0` via `Tunnel10 track 10`.
- `BRANCH-B-RTR` uses `track 30` with `ip sla 30`, probing `172.16.30.1` from `Tunnel30`, and installs `0.0.0.0/0` via `Tunnel30 track 30`.
- Branch tunnel delays indicate intended path preference: `PRIMARY` tunnels use delay `1000`, `BACKUP` tunnels use delay `20000`.
- Branch routers provide inter-VLAN routing for local users.
- `BRANCH-A-RTR` serves `192.168.10.0/24`, `192.168.20.0/24`, and `192.168.99.0/24`.
- `BRANCH-B-RTR` serves `192.168.110.0/24`, `192.168.120.0/24`, and `192.168.199.0/24`.
- Branch switches are L2 only and rely on router trunk uplinks plus `Vlan99` SVI management.
- `BRANCH-A-Switch` management IP is `192.168.99.11`, default gateway `192.168.99.1`.
- `BRANCH-B-Switch` management IP is `192.168.199.11`, default gateway `192.168.199.1`.
- `LAB-MGMT-BR01` has an external default route to `192.168.1.1` and participates in OSPF with both HQ cores.

## Routing Evidence Worth Preserving For LLM
- Branch routers learn HQ, peer branch, and external prefixes via `EIGRP`, with the primary tunnel acting as the current default path.
- HQ distribution nodes learn branch LANs and branch loopbacks from EIGRP and learn HQ/core/internet context from OSPF.
- `HQ-CORE-RT01` holds live BGP routes for `198.51.100.0/24` and `203.0.113.0/24`.
- `HQ-CORE-RT02` and `LAB-MGMT-BR01` learn those external routes as `OSPF external`, which confirms redistribution from the active BGP edge.

## LLM-Ready Structured Facts To Store
- `device_role`, `site`, `os_platform`, `loopback`, `mgmt_ip`
- `physical_neighbors`: local interface, remote device, remote interface, remote IP, managed or external
- `logical_neighbors`: protocol, local interface, neighbor IP, neighbor router-id or ASN, state
- `branch_path_role`: primary or backup per tunnel
- `tracked_default_routes`: track id, IP SLA id, probe target, source interface, current state
- `redistribution_points`: device, source protocol, target protocol
- `external_dependencies`: ISP peer, provider trunk, management upstream
- `branch_lans`: VLAN ids, subinterfaces, default gateways, switch mgmt SVIs
- `automation_eligibility`: managed device, external device, L2-only node, internet edge, redistribution node

## Recommended Read-Only Collection Set For Future Automation
- Core / distribution routers: `show ip interface brief`, `show cdp neighbors detail`, `show ip protocols`, `show ip route`, `show ip ospf neighbor`, `show ip eigrp neighbors`, `show ip bgp summary`, `show running-config | section router`, `show running-config | section interface Tunnel`, `show track`, `show ip sla summary`
- Branch routers: `show ip interface brief`, `show cdp neighbors detail`, `show ip route`, `show ip eigrp neighbors`, `show track`, `show ip sla summary`, `show running-config | section interface Tunnel`, `show running-config | section interface GigabitEthernet0/3`, `show running-config | section track`
- Access switches: `show interfaces status`, `show interfaces trunk`, `show vlan brief`, `show cdp neighbors detail`, `show ip default-gateway`, `show running-config | section interface`

## High-Value Automation Scenarios Enabled By Current Lab
- Detect and classify `primary tunnel failure` on either branch from `track`, `IP SLA`, `default route`, and `EIGRP neighbor` evidence
- Detect `backup path takeover` versus `complete branch isolation`
- Detect `HQ redistribution fault` when branch routes vanish from OSPF or EIGRP domains
- Detect `internet edge fault` when BGP to `100.66.0.2` drops on `HQ-CORE-RT01`
- Detect `branch access uplink fault` when switch trunk or router subinterface path fails
- Distinguish `provider / external fault` from `internal config drift`
- Generate approval-gated remediation proposals for config drift on tunnels, track/IP SLA, branch trunk subinterfaces, or protocol stanzas

## Blind Spots And Next Additions
- `ISP-RT` appears in CDP but is not in inventory, so it is currently an external dependency and not a managed automation target
- Provider circuits are represented by subinterfaces and descriptions, not by dedicated managed inventory devices
- No long-term utilization metrics or interface error history were collected in this snapshot
- No persistent config-baseline store exists yet; this report used live targeted config collection only
- `HQ-CORE-RT02` is not currently an active BGP edge in the observed evidence; if failover is expected there, that design intent is not yet visible from live config

## Bottom Line
- The lab is already rich enough for meaningful `LLM-first` automation.
- The most important model context is not just device inventory, but the dependency chain: `BGP edge -> OSPF core -> OSPF/EIGRP redistribution at HQ dist -> primary/backup GRE/IPsec branch tunnels -> branch router trunk -> access switch VLANs`.
- If this dependency map is stored as structured evidence and refreshed continuously, the LLM can reason about blast radius, likely root cause, safe remediation targets, and when it should escalate instead of auto-fixing.
