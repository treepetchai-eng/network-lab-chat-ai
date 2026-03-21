# Incident-First LLM Ops Demo สำหรับ EVE-NG

## Summary
- เป้าหมายคือยกระดับ repo นี้ให้เป็น live `LLM-first` network ops demo ที่ชนะระบบ monitoring แบบเก่าในเรื่อง detect, investigate, blast radius, approval-gated self-healing, verify และ close incident แบบใช้ข้อมูลจริงทั้งหมด
- คงหลัก `LLM-first free-run` เดิมไว้: backend ทำหน้าที่เก็บ facts, execute tools, schedule jobs, preserve evidence; LLM ยังเป็นคนตีความ evidence, ตัดสินใจว่าจะเช็กอะไรเพิ่ม, สรุป root cause, และเขียนข้อเสนอ remediation
- deployment ที่ใช้สำหรับเดโม: `Dedicated ops VM` รัน FastAPI, `ops_worker`, PostgreSQL, `syslog-ng`, และ snapshot archive; ใน EVE-NG คง 9 devices เดิมและขยายแบบ moderate โดยเพิ่ม 1 upstream `ISP/WAN simulator` และ 1 `app/service target`
- v1 จะเน้นแทน legacy monitoring ในมุม incident operations; ยังไม่ทำ full SNMP/flow analytics, auth จริง, หรือ autonomous config write แบบไม่ผ่าน approval

## ข้อมูลที่ต้องมี
- Inventory ต้องมีอย่างน้อย: `hostname`, `mgmt_ip`, `site`, `device_role`, `os_platform`, `version`, `criticality`, `service_tags`, `auto_remediation_enabled`, `upstream/provider tag`, `maintenance window tag`
- Evidence ที่ต้องเก็บต่อเนื่อง: raw syslog, normalized events, periodic CLI snapshots, physical/logical topology edges, config baselines, execution/verification outcomes, operator feedback
- Snapshot facts ที่ต้องแปลงเป็น structured data: device reachability, interface admin/oper state, routing neighbor state, default-route presence, `IP SLA`/`track` state, CPU summary, recent error counters, selected config section hashes
- Config baseline ต้องเก็บ: last-known-good targeted sections, current diff, rollback commands, verify commands, capture timestamp, source incident/job
- Remediation metadata ต้องเก็บ: template/action id, supported platform, blast-radius class, prechecks, verify steps, rollback strategy, approval requirement, `auto_execute_eligible=false` สำหรับ v1

## Implementation Changes
- ต่อยอดของเดิม ไม่รื้อของเดิม: ใช้ syslog ingest, incident correlation, ops loop, approval flow ที่มีอยู่แล้ว แล้วเติม evidence layer และ topology/context layer
- เพิ่ม deterministic snapshot collector ที่วิ่งตาม schedule และถูก trigger ทันทีเมื่อมี incident ใหม่
- Router/core/dist profile: `show ip interface brief`, `show ip bgp summary`, `show ip ospf neighbor`, `show ip eigrp neighbors`, `show ip route summary`, `show ip sla summary`, `show track`, `show cdp neighbors detail`, `show processes cpu sorted | include CPU utilization`
- Access-switch profile: `show interfaces status`, `show interfaces trunk`, `show vlan brief`, `show spanning-tree summary`, `show mac address-table count`, `show ip default-gateway`, `show cdp neighbors detail`
- เพิ่ม targeted config baseline capture ด้วย `show running-config | section` สำหรับ interface, routing protocol, static route, `IP SLA`, `track`, route-map, prefix-list; ไม่ dump full config โดย default
- เพิ่ม generic parsers ที่แปลง snapshot outputs เป็น exact facts เช่น `neighbor_down`, `default_route_missing`, `track_down`, `device_unreachable`, `cpu_high`; backend ออก fact ได้ แต่ LLM ต้องเป็นคนวินิจฉัยและสรุป
- เพิ่ม topology discovery จาก `CDP/LLDP` และ control-plane neighbors โดยแยก physical edges กับ logical edges และ attach เข้า incident evidence
- ขยาย action catalog ให้มี template ที่ใช้จริงได้ใน lab: restore missing interface stanza, restore missing static/default route, restore BGP/OSPF/EIGRP config from baseline, soft clear BGP, reapply `IP SLA`/`track` config; `bounce tunnel` คง approval-only
- กำหนด remediation classes ชัดเจน: config drift เปิด proposal ได้, physical/link/provider fault ต้อง escalate, action ที่มี site-wide blast radius หรือ reload semantics ยังคง blocked
- รัน automation ผ่าน `ops_worker` แยก process บน ops VM; v1 ใช้ Postgres-backed jobs ต่อไป ไม่เพิ่ม Redis/Celery ถ้ายังไม่จำเป็น
- Trigger evidence enrichment ทันทีหลังสร้าง incident และคง periodic fallback scheduler ไว้สำหรับ missed runs

## Public APIs / Types
- เพิ่ม entities: `device_snapshots`, `snapshot_facts`, `topology_edges`, `config_snapshots`, `config_baselines`, `detected_conditions`
- ขยาย device payload ให้มี `criticality`, `service_tags`, `auto_remediation_enabled`, `last_snapshot_at`, `health_status`, `config_drift_status`
- ขยาย incident payload ให้มี `evidence_timeline`, `blast_radius`, `topology_context`, `latest_condition_set`, `remediation_class`
- เพิ่ม endpoints: `GET /api/ops/topology`, `GET /api/ops/health-matrix`, `GET /api/ops/devices/{id}/snapshots`, `GET /api/ops/incidents/{id}/evidence`, `POST /api/ops/devices/{id}/collect-now`, `POST /api/ops/devices/{id}/capture-baseline`
- เพิ่ม UI surfaces: topology page, health matrix บน overview, device snapshot/config-drift panels, incident evidence timeline, approval panel ที่โชว์ baseline diff + rollback + verify output

## Demo Scenarios และ Acceptance
- Scenario 1: ลบ routing/interface stanza บนอุปกรณ์จริงใน EVE-NG; ต้องเห็น syslog incident, snapshot enrichment, AI ชี้ว่าเป็น config drift, สร้าง approval proposal จาก baseline diff, execute จริงหลัง approve, verify จริง, และปิด incident ได้
- Scenario 2: ลบ static/default route; ต้องเห็น missing-route condition, topology impact, AI เสนอ restore route, verify ด้วย route/reachability checks, และ resolved หลัง approve
- Scenario 3: ทำ upstream ISP/service path หรือ `IP SLA` target ให้ล้ม; ต้องเห็น track/IP SLA incident, AI classify ว่า external/provider หรือ upstream dependency, ไม่มี proposal ที่เสี่ยง, และ escalate ด้วย evidence ชัด
- Scenario 4: shut uplink/trunk หรือ router-facing interface; ต้องเห็น clustered incidents หลาย device, blast radius ผ่าน topology, AI หาสาเหตุร่วมได้, และไม่มั่วเสนอ self-heal ถ้าเป็น physical/manual issue
- ห้าม insert incident/event ปลอมเข้าฐานข้อมูลเพื่อเดโม; ทุกอย่างต้องมาจาก fault จริง, syslog จริง, และ CLI evidence จริง
- acceptance thresholds: incident ขึ้น UI ภายใน 15 วินาทีหลัง fault, enrichment เสร็จภายใน 60 วินาที, AI diagnosis/proposal ภายใน 120 วินาที, verification ภายใน 120 วินาทีหลัง approve
- เดโมต้องมีอย่างน้อย 1 เคสที่ “heal ได้หลัง approval” และ 1 เคสที่ “ระบบปฏิเสธ automation อย่างถูกต้องและ escalate”

## Test Plan
- Unit tests สำหรับ parsers, topology extraction, config baseline diffing, condition synthesis, และ action-template selection
- Integration tests สำหรับ flow เต็ม: syslog push -> incident -> snapshot collection -> AI troubleshoot -> approval -> execution -> verification -> auto-close
- Live lab tests สำหรับ 4 scenarios ข้างต้นบน EVE-NG จริง พร้อมเก็บ timestamps, raw evidence, approvals, และ final incident states
- Regression checks ต้องยืนยันว่า evidence ถูก preserve, fact counts ตรงกับ execution, ไม่มี inventory-only health claims, ไม่มี scenario-specific hardcoding, และ write actions ยัง approval-gated

## Assumptions / Defaults
- ใช้ PostgreSQL ที่ migrate ถึง Alembic `head` เป็น operational store หลัก; SQLite file ใน repo ถือว่าเป็น dev artifact ที่ schema ตามหลัง
- รักษา `LLM-first free-run` ไว้เต็มรูป; collectors/parsers เพิ่ม facts ไม่ใช่ hardcoded conclusions
- `Full automate` ใน v1 หมายถึง automate detection, enrichment, reasoning, proposal, verification, และ closure ให้ได้มากที่สุดก่อนถึง approval boundary
- v1 ยังไม่รวม SNMP/NetFlow; ถ้าต้องเพิ่ม parity ด้าน performance monitoring ค่อยต่อด้วย SNMP polling ใน phase ถัดไป
- การ save startup-config ยังไม่อยู่ใน automated path ของ v1 จนกว่าจะเพิ่ม explicit approval action สำหรับ persistence โดยเฉพาะ
