# Detailed EVE-NG Automation Implementation Plan

## 1. Purpose

เอกสารนี้เป็นแผนลงมือทำต่อจาก repo ปัจจุบัน เพื่อให้ระบบไปถึงจุดที่ “สมบูรณ์สำหรับ EVE-NG scope” ในรูปแบบ `incident-first`, `LLM-first`, `evidence-first`, และ `approval-gated exact-restore`.

คำว่า “สมบูรณ์” ในบริบทนี้ไม่ได้หมายถึงแทนระบบ NMS ทุกมิติ แต่หมายถึงระบบสามารถทำงานครบวงจรแบบนี้ได้จากข้อมูลจริง:

- monitor ตัวเองจาก `syslog + periodic CLI snapshots`
- สร้าง topology / dependency context จาก live evidence
- detect และ correlate incident
- enrich incident ด้วย facts ที่ใช้งานได้จริง
- ให้ LLM วิเคราะห์ root cause และ blast radius
- สร้าง `exact-restore proposal` สำหรับเคส deterministic
- ให้คน `approve`
- execute และ verify จาก syslog + CLI
- close หรือ escalate อย่างมีเหตุผล

เอกสารนี้ตั้งต้นจากข้อเท็จจริงที่ตรวจแล้วใน lab:

- SSH เข้าอุปกรณ์ได้ `9/9`
- มี `OSPF`, `EIGRP`, `BGP`, `GRE/IPsec`, `IP SLA`, `track`, branch trunk, switch default-gateway และ config sections ที่ใช้เป็น intended state ได้จริง
- current repo มี syslog ingest, incident correlation, LLM investigation, approval flow, execution flow, ops console, audit trail และ worker/runtime พื้นฐานอยู่แล้ว

## 2. Target End State

เมื่อแผนนี้เสร็จ ระบบควรทำได้ดังนี้:

- เก็บ snapshot facts ของทุกอุปกรณ์ตามรอบเวลา และตาม event trigger
- รู้ physical links, logical neighbors, primary/backup tunnel roles, redistribution points, external dependencies
- รู้ว่า config/state ใด “ผิดจาก intended state”
- สรุปได้ว่า incident เป็น
  - config problem
  - failover event
  - branch isolation
  - HQ redistribution issue
  - internet edge issue
  - upstream/provider issue
- สร้าง remediation proposal แบบ `exact restore` สำหรับ deterministic branch-scoped faults
- ไม่แก้เองโดยพลการ แต่สร้าง proposal พร้อม rationale, rollback, verify steps ให้ operator อนุมัติ
- execute และ verify ได้จริงบน live lab
- ถ้าไม่ควรแก้เอง ต้องอธิบายเหตุผลและ escalate ให้ถูก

## 3. Non-Negotiable Design Rules

แผนนี้ต้องไม่ทำลายหลักของ repo:

- ต้องคง `LLM-first free-run`
- ห้ามใช้ mock incidents, mock health state, หรือ mock topology ใน product flow
- ห้ามย้าย reasoning หลักไปเป็น hardcoded backend conclusions
- write/config actions ต้องยัง `approval-gated`
- backend ทำหน้าที่ facts, evidence preservation, execution, policy, consistency
- LLM ทำหน้าที่ตีความ evidence, วินิจฉัย, เลือก scope, สรุป blast radius, และเขียน proposal/explanation

## 4. Current State In Repo

สิ่งที่มีอยู่แล้วและควรต่อยอด ไม่ควรรื้อ:

- `backend/src/ops/syslog_ingest.py` และ `backend/src/ops/syslog_parser.py`
  เก็บ raw logs และ parse เป็น normalized events ได้แล้ว
- `backend/src/ops/incidents.py`
  correlate events เป็น incidents ได้แล้ว
- `backend/src/ops/ai.py`
  มี LLM analysis และ investigation flow อยู่แล้ว
- `backend/src/ops/free_run.py`
  มี incident-scoped troubleshoot และ structured output shaping อยู่แล้ว
- `backend/src/ops/service.py`
  มี incident, approval, execution, job, device detail, audit, artifact flow อยู่แล้ว
- `backend/src/ops/ops_loop.py`
  มี auto-investigate, troubleshoot, execute, verify, auto-close loop พื้นฐานแล้ว
- `backend/src/ops/runtime.py` และ `backend/src/ops_worker.py`
  มี scheduler และ worker process พร้อมใช้
- `frontend/src/app/ops/*`
  มี ops console, incidents, devices, approvals, clusters อยู่แล้ว

ข้อสรุปคือ repo นี้ไม่ได้เริ่มจากศูนย์ สิ่งที่ต้องทำต่อคือ “เติม evidence layer และ intended-state reasoning layer” ให้หนาพอสำหรับ automation.

## 5. Scope ที่ควรทำบน EVE-NG

สิ่งที่อยู่ใน scope:

- syslog-driven incident awareness
- periodic CLI state collection
- topology and dependency graph
- intended state / baseline capture
- drift detection
- exact-restore proposal generation
- approval-gated execution
- post-change verification

สิ่งที่อยู่นอก scope รอบนี้:

- bandwidth monitoring
- interface error rate / CRC / queue depth monitoring
- real performance analytics แบบ production
- multi-vendor automation
- autonomous config apply โดยไม่ต้อง approval

## 6. Fault Classes We Intend To Support

ระบบควร monitor, explain, และอย่างน้อยสร้าง proposal ได้สำหรับ:

- primary tunnel down แล้ว branch fail ไป backup
- `IP SLA` / `track` down
- branch default route หาย
- tunnel config หาย
- routing protocol stanza หาย
- redistribution config หาย
- branch trunk/subinterface config หาย
- BGP edge down บน `HQ-CORE-RT01`
- branch access uplink down

ระบบควรสร้าง `exact-restore proposal` อัตโนมัติสำหรับเคส deterministic เหล่านี้ก่อน:

- missing branch default route
- missing branch `track`
- missing branch `ip sla` config or schedule
- wrong or missing branch switch default-gateway
- missing branch tunnel stanza
- missing branch subinterface stanza

ระบบควร `detect + explain + escalate` สำหรับ:

- BGP edge issue
- HQ redistribution issue
- provider / upstream issue
- physical uplink issue
- unclear or multi-cause failures

## 7. Detailed Implementation Phases

## Phase 1: Persistent Evidence Layer

### Goal

ทำให้ระบบมีที่เก็บ facts จาก periodic CLI collection แบบถาวรใน PostgreSQL ไม่ต้องอาศัยแค่ syslog กับ output ชั่วคราวใน memory

### Why

ตอนนี้ repo เก่งเรื่อง syslog และ ad hoc troubleshooting แล้ว แต่ยังไม่มี evidence layer สำหรับ “steady-state truth”. ถ้าไม่มีชั้นนี้ ระบบจะรู้ว่าเกิด incident แต่ไม่รู้ว่าปัจจุบัน intended state กับ live state ต่างกันตรงไหน

### Required Changes

- เพิ่มตารางใหม่ใน `backend/src/ops/models.py`
  - `device_snapshots`
  - `snapshot_facts`
  - `topology_edges`
  - `config_baselines`
  - `detected_conditions`
- เพิ่ม Alembic migration ใหม่ใน `backend/migrations/versions/`
- `device_snapshots`
  ต้องเก็บ `device_id`, `snapshot_type`, `collected_at`, `collector`, `raw_output_json`, `status`, `error_text`
- `snapshot_facts`
  ต้องเก็บ fact แบบ normalized เช่น `fact_type`, `fact_key`, `fact_value_json`, `severity`, `source_snapshot_id`
- `topology_edges`
  ต้องเก็บ `edge_type`, `local_device_id`, `remote_device_id`, `remote_label`, `local_interface`, `remote_interface`, `protocol`, `state`, `metadata_json`
- `config_baselines`
  ต้องเก็บ intended state แบบ targeted section ไม่ใช่ full config ทั้งก้อน
- `detected_conditions`
  ต้องเก็บ condition taxonomy ที่ engine ตรวจพบ เช่น `missing_branch_default_route`

### Deliverables

- migration ใช้งานได้บน PostgreSQL จริง
- API และ service layer สามารถอ่าน snapshot ล่าสุดต่อ device ได้
- device detail endpoint เริ่มดึง latest snapshot / latest conditions ได้

### Done Criteria

- เก็บ snapshot และ facts ลง DB ได้จริง
- snapshot facts ถูก query ได้ตาม device และตามเวลา
- topology edges และ conditions ถูก persist ได้

## Phase 2: Periodic CLI Snapshot Collector

### Goal

สร้าง collector ที่วิ่งทุก `30-60` วินาที และเก็บ command set ตาม device role

### Why

syslog อย่างเดียวไม่พอสำหรับบอกว่า default route หาย, track หาย, switch default-gateway ผิด, หรือ tunnel stanza หาย Collector คือแหล่ง truth ที่ทำให้ระบบ “monitor ตัวเอง” ได้จริง

### Required Changes

- เพิ่ม module ใหม่ เช่น `backend/src/ops/snapshot_collect.py`
- เพิ่ม parser/collector orchestration ใหม่ เช่น `backend/src/ops/snapshot_service.py`
- ผูกเข้ากับ `backend/src/ops/runtime.py` และ `backend/src/ops_worker.py`
- เพิ่ม env vars เช่น
  - `SNAPSHOT_INITIAL_RUN`
  - `SNAPSHOT_INTERVAL_SECONDS`
  - `SNAPSHOT_ON_INCIDENT`
  - `SNAPSHOT_MAX_PARALLEL_DEVICES`

### Command Set By Role

- Core / distribution routers
  - `show ip interface brief`
  - `show cdp neighbors detail`
  - `show ip protocols`
  - `show ip route`
  - `show ip ospf neighbor`
  - `show ip eigrp neighbors`
  - `show ip bgp summary`
  - `show track`
  - `show ip sla summary`
  - `show running-config | section router`
  - `show running-config | section interface Tunnel`
- Branch routers
  - `show ip interface brief`
  - `show cdp neighbors detail`
  - `show ip route`
  - `show ip eigrp neighbors`
  - `show track`
  - `show ip sla summary`
  - `show running-config | section interface Tunnel`
  - `show running-config | section interface GigabitEthernet0/3`
  - `show running-config | section track`
  - `show running-config | section ip sla`
- Access switches
  - `show interfaces status`
  - `show interfaces trunk`
  - `show vlan brief`
  - `show cdp neighbors detail`
  - `show ip default-gateway`
  - `show running-config | section interface`

### Deliverables

- worker รัน periodic snapshot ได้เอง
- incident ใหม่ trigger urgent snapshot ได้
- collector แยก success / failure ต่อ device ชัดเจน

### Done Criteria

- ทุก device ใน inventory ถูก snapshot ได้
- มี latest snapshot ต่อ device ใน DB
- ถ้า device เข้าไม่ได้ ระบบ mark condition เป็น reachability issue ได้

## Phase 3: Topology And Dependency Graph

### Goal

ทำ topology/dependency model ให้ query และใช้ reasoning ได้จริง

### Why

LLM จะวิเคราะห์ blast radius ได้ดี ก็ต่อเมื่อมี graph ที่บอกว่าใครเชื่อมกับใคร, protocol อะไร, และ primary/backup path คืออะไร

### Required Changes

- ใช้ผลจาก `show cdp neighbors detail`, `show ip ospf neighbor`, `show ip eigrp neighbors`, `show ip bgp summary`, และ targeted interface config
- สร้าง parser functions ใหม่ เช่น
  - parse physical edges
  - parse OSPF adjacencies
  - parse EIGRP adjacencies
  - parse BGP peering
  - parse branch WAN tunnel roles
  - parse trunk relationships
- บันทึก topology ลง `topology_edges`
- เพิ่ม graph builder / summarizer ที่รวม edges ให้เป็น incident context
- ขยาย `backend/src/ops/service.py`
  เพื่อให้ incident detail และ device detail ดึง topology context ได้
- เพิ่ม endpoint ใหม่
  - `GET /api/ops/topology`
  - `GET /api/ops/incidents/{id}/evidence`

### Deliverables

- topology graph ที่สะท้อน lab จริง
- physical and logical relationships ถูกแยกชัด
- external dependency เช่น `ISP-RT` ถูก mark เป็น external node

### Done Criteria

- system สามารถตอบได้ว่า branch ไหนพึ่ง HQ-DIST ตัวไหน
- system สามารถตอบได้ว่า internet edge อยู่ที่ใคร
- system สามารถตอบได้ว่า branch uplink และ switch mgmt path ผ่านใคร

## Phase 4: Intended State And Config Baseline

### Goal

สร้าง intended-state store ที่ใช้เปรียบเทียบ current state กับ baseline แบบ exact

### Why

ถ้าจะพูดว่า “มันหาย” หรือ “มันไม่ถูกต้อง” ระบบต้องมีแหล่ง truth ว่าควรจะเป็นอะไร การไม่มี baseline จะทำให้ proposal กลายเป็นการเดา

### Required Changes

- เพิ่ม service ใหม่ เช่น `backend/src/ops/baseline.py`
- เพิ่ม baseline capture flow แบบ targeted section
- baseline ไม่เก็บ full running-config ก่อนในรอบแรก
- baseline sections ที่ต้องมีสำหรับ lab นี้
  - branch default route
  - branch `track`
  - branch `ip sla`
  - branch tunnel stanzas
  - branch subinterfaces
  - switch default-gateway
  - HQ redistribution stanzas
  - HQ BGP edge config summary
- baseline source อาจมาจาก
  - operator capture ตอน state ปกติ
  - first known-good snapshot ที่ operator ยืนยัน

### Deliverables

- baseline ต่อ device / section
- baseline versioning พร้อม timestamp และ actor
- API สำหรับดู current vs baseline

### Done Criteria

- system สามารถอ่าน intended state ต่อ branch/router/switch ได้
- system สามารถรู้ว่า command/proposal ที่จะ restore คือ exact content อะไร

## Phase 5: Drift Detection Engine

### Goal

สร้าง deterministic engine ที่ตรวจ condition จาก current facts เทียบ baseline

### Why

ส่วนนี้ควรเป็น deterministic fact engine ไม่ใช่ LLM เพราะมันคือ “exact mismatch detection”. เมื่อ engine ตรวจ condition ได้แล้ว ค่อยส่งผลสรุปเข้า LLM เพื่อทำ reasoning ชั้นสูง

### Required Changes

- เพิ่ม module ใหม่ เช่น `backend/src/ops/drift_detection.py`
- ตรวจ condition ต่อไปนี้อย่างน้อย:
  - `missing_branch_default_route`
  - `missing_branch_track_binding`
  - `missing_branch_ip_sla`
  - `wrong_branch_switch_default_gateway`
  - `missing_branch_tunnel_stanza`
  - `missing_branch_subinterface`
  - `branch_failover_active`
  - `bgp_edge_down`
  - `hq_redistribution_fault`
  - `branch_access_uplink_down`
  - `device_unreachable`
- แต่ละ condition ต้องมี
  - `condition_type`
  - `severity`
  - `device_id`
  - `evidence_json`
  - `baseline_ref`
  - `explanation_seed`

### Deliverables

- detected conditions ถูกสร้างจาก snapshot cycle
- incident enrichment สามารถแนบ conditions เข้า incident ได้
- UI และ LLM อ่าน conditions ได้

### Done Criteria

- ถ้า route หรือ track หาย ระบบจับได้โดยไม่ต้องรอให้ LLM เดา
- ถ้า branch fail ไป backup ระบบจับได้และไม่สับสนกับ total outage

## Phase 6: LLM Context Builder And Diagnosis Layer

### Goal

ทำให้ LLM รับ context ที่มีคุณภาพสูงขึ้น และตอบแบบ reasoning จริงจาก evidence ที่จัดรูปแล้ว

### Why

ตอนนี้ LLM ใช้ incident + tool output ได้แล้ว แต่ถ้ามี structured facts, topology context, drift conditions, และ baseline comparison อยู่ด้วย คุณภาพ diagnosis และ proposal จะดีขึ้นมาก โดยไม่ต้อง hardcode answer logic

### Required Changes

- เพิ่ม context builder ใหม่ เช่น `backend/src/ops/llm_context.py`
- ใส่ inputs ต่อไปนี้เข้า prompt/investigation payload:
  - incident timeline
  - latest snapshot facts
  - topology summary
  - detected conditions
  - baseline comparison
  - blast-radius seed
  - managed vs external dependency flags
- ปรับ `backend/src/ops/ai.py` และ `backend/src/ops/free_run.py`
  ให้ใช้ structured incident context มากขึ้น
- เพิ่ม diagnosis taxonomy ใน structured result:
  - `config_problem`
  - `failover_event`
  - `branch_isolation`
  - `hq_redistribution_issue`
  - `internet_edge_issue`
  - `upstream_provider_issue`
  - `uncertain`

### Deliverables

- LLM summary ที่สม่ำเสมอขึ้น
- blast radius explanation ดีขึ้น
- reason ที่บอกว่า “ควร restore” หรือ “ควร escalate” ชัดขึ้น

### Done Criteria

- LLM ใช้ facts จาก topology + condition + baseline ได้จริง
- final summary ไม่มีการเดาสุ่มข้าม evidence

## Phase 7: Proposal Engine For Approval-Gated Exact Restore

### Goal

สร้าง proposal generator ที่ไม่แก้เอง แต่สร้าง `exact-restore proposal` สำหรับ deterministic branch-scoped cases

### Why

นี่คือรูปแบบที่เหมาะที่สุดสำหรับ lab นี้และตรงกับ policy repo มากที่สุด ระบบควรเก่งที่ “รู้ว่าผิดอะไรและเสนอ restore แบบ exact” มากกว่าการ execute เองแบบ autonomous

### Required Changes

- เพิ่ม module เช่น `backend/src/ops/proposal_engine.py`
- proposal types ชุดแรก:
  - `restore_branch_default_route`
  - `restore_branch_track_binding`
  - `restore_branch_ip_sla_exact`
  - `restore_branch_switch_default_gateway`
  - `restore_branch_tunnel_exact`
  - `restore_branch_subinterface_exact`
- แต่ละ proposal ต้องสร้าง:
  - title
  - what is wrong now
  - why it is incorrect
  - evidence refs
  - exact restore commands
  - rollback commands
  - verify commands
  - blast radius summary
  - required approval role
- ขยาย `backend/src/ops/service.py`
  ให้รับ proposal จาก engine แล้ว create approval row อัตโนมัติ

### Deliverables

- deterministic branch faults สร้าง approval proposal ได้เอง
- proposal ใช้ baseline จริง ไม่ใช่ prompt-only generation

### Done Criteria

- เมื่อ route/track/ip sla/tunnel/subinterface/gateway หาย ระบบสร้าง proposal exact restore ได้
- proposal อธิบายได้ว่าทำไมถึงผิดและ verify อย่างไร

## Phase 8: Approval, Execution, Verification, Closure

### Goal

ทำให้ post-approval loop เชื่อถือได้และอธิบาย outcome ได้ชัด

### Why

การมี proposal อย่างเดียวไม่พอ ต้องพิสูจน์ว่าหลัง execute แล้ว incident ดีขึ้นจริง ไม่อย่างนั้น demo จะดูเหมือน config runner ธรรมดา

### Required Changes

- ใช้ execution path เดิมใน `backend/src/ops/service.py`
- ขยาย verify logic ใน `backend/src/ops/ops_loop.py`
  ให้ตรวจทั้ง
  - syslog recovery
  - latest snapshot facts
  - verify commands จาก proposal
- เพิ่ม remediation outcome taxonomy:
  - `verified_recovered`
  - `partially_recovered`
  - `still_failed`
  - `verification_inconclusive`
  - `requires_escalation`
- ทำ incident transition rules ให้ชัด:
  - verified -> `monitoring` -> `resolved`
  - fail / inconclusive -> `in_progress` หรือ `acknowledged`
  - provider / physical -> `acknowledged` + `escalated`

### Deliverables

- proposal ที่ approve แล้ว execute ได้จริง
- verify ใช้ facts จริง ไม่ใช่แค่ exit code
- incident state machine สะอาดขึ้น

### Done Criteria

- deterministic demo scenario อย่างน้อย 1 เคส วิ่งได้ครบ `detect -> propose -> approve -> execute -> verify -> close`
- failed verification กลับไปสถานะที่เหมาะสมได้

## Phase 9: UI For Evidence, Drift, And Proposal Review

### Goal

ทำให้หน้า ops console เห็น “reasoning substrate” ของระบบ ไม่ใช่แค่ incident list

### Why

ถ้าจะเอาไปเสนอ ระบบต้องโชว์ว่า AI ไม่ได้เดา แต่ใช้ topology, baseline, current state, และ exact restore reasoning จริง

### Required Changes

- เพิ่มหน้า / panel ใหม่ใน frontend
  - topology / dependency page
  - device snapshots page or section
  - config drift / baseline diff section
  - incident evidence timeline
  - proposal review section ที่แสดง exact restore / rollback / verify
- ขยาย types ใน `frontend/src/lib/ops-types.ts`
- ขยาย API client ใน `frontend/src/lib/ops-api.ts`
- อัปเดต incident detail page ให้มี section:
  - topology context
  - latest detected conditions
  - baseline mismatch
  - proposal rationale

### Deliverables

- demo operator เห็นได้ว่าระบบใช้ facts อะไรตัดสิน
- approval UI ไม่ใช่แค่แสดง command text แต่แสดง context ด้วย

### Done Criteria

- incident หนึ่งรายการสามารถเปิดดูได้ครบทั้ง logs, facts, topology, drift, proposal, verify result

## Phase 10: Demo Scenario Pack And Verification

### Goal

ทำ live scenario pack ที่พิสูจน์ว่าระบบสมบูรณ์สำหรับ EVE-NG scope จริง

### Why

ระบบจะน่าเชื่อถือก็ต่อเมื่อมี scenario pack ที่ repeat ได้ ไม่ใช่เดโมสดแบบเฉพาะหน้า

### Required Scenario Pack

- Scenario A: primary tunnel down -> failover active -> classify correctly -> no unsafe proposal
- Scenario B: branch default route removed -> create restore proposal -> approve -> verify -> close
- Scenario C: track statement removed -> create restore proposal -> approve -> verify -> close
- Scenario D: ip sla schedule removed -> create restore proposal -> approve -> verify -> close
- Scenario E: branch switch default-gateway wrong -> create restore proposal -> approve -> verify -> close
- Scenario F: BGP edge down on `HQ-CORE-RT01` -> detect -> explain -> escalate
- Scenario G: HQ redistribution issue -> detect -> explain -> escalate

### Testing Changes

- เพิ่ม unit tests สำหรับ parsers และ drift detectors
- เพิ่ม integration tests สำหรับ snapshot -> conditions -> proposal flow
- เพิ่ม e2e tests สำหรับ approval/execution/verification
- เพิ่ม live runbook สำหรับ fault injection ใน EVE-NG

### Done Criteria

- มีอย่างน้อย `5` deterministic scenarios ที่จบครบวงจร
- มีอย่างน้อย `2` high-risk / external scenarios ที่ระบบ escalate ถูกต้อง

## 8. Recommended File-Level Change Map

ไฟล์ที่น่าจะแก้หรือเพิ่มแน่ ๆ:

- `backend/src/ops/models.py`
- `backend/src/ops/service.py`
- `backend/src/ops/runtime.py`
- `backend/src/ops/ops_loop.py`
- `backend/src/ops/ai.py`
- `backend/src/ops/free_run.py`
- `backend/src/api.py`
- `frontend/src/lib/ops-types.ts`
- `frontend/src/lib/ops-api.ts`
- `frontend/src/app/ops/incidents/[id]/page.tsx`
- `frontend/src/app/ops/devices/page.tsx`
- `frontend/src/app/ops/page.tsx`

ไฟล์ใหม่ที่ควรเพิ่ม:

- `backend/src/ops/snapshot_collect.py`
- `backend/src/ops/snapshot_parse.py`
- `backend/src/ops/baseline.py`
- `backend/src/ops/drift_detection.py`
- `backend/src/ops/proposal_engine.py`
- `backend/src/ops/llm_context.py`
- `backend/src/ops/topology.py`
- `frontend/src/app/ops/topology/page.tsx`

## 9. Suggested Delivery Order

ลำดับที่ควรทำเพื่อให้คุ้มแรงและเห็นผลเร็ว:

1. DB schema for snapshots, baselines, topology, conditions
2. periodic snapshot collector
3. topology graph persistence
4. baseline capture
5. drift detection
6. LLM context builder
7. exact-restore proposal engine
8. verify-and-close loop hardening
9. UI evidence and review panels
10. demo scenario pack

## 10. Definition Of Complete For EVE-NG Scope

ถือว่าระบบ “สมบูรณ์” สำหรับ EVE-NG เมื่อครบทั้งหมดนี้:

- snapshot collection ทำงานอัตโนมัติ
- topology/dependency graph ถูกสร้างจากข้อมูลจริง
- intended state ถูกเก็บแบบ targeted baseline
- drift detection จับ deterministic faults ได้
- incident enrichment ผูก logs + snapshots + topology + drift ได้
- LLM วิเคราะห์เหตุการณ์ได้จาก evidence structure ที่ครบ
- deterministic branch faults สร้าง exact-restore proposal ได้เอง
- proposal ผ่าน approval, execute, verify, close ได้จริง
- high-risk / external faults ถูก escalate อย่างถูกต้อง
- demo ใช้ข้อมูลจริงทั้งหมด ไม่มี mock

## 11. Final Recommendation

จุดสำคัญที่สุดคืออย่าพยายามทำ “autonomous remediation” ให้เร็วเกินไป

สำหรับ lab นี้ แนวทางที่แข็งแรงที่สุดคือ:

- `autonomous monitoring`
- `autonomous evidence building`
- `autonomous diagnosis`
- `autonomous exact-restore proposal generation`
- `human-approved execution`
- `autonomous verification and closure`

นี่คือ sweet spot ที่ทั้งสมจริง, สื่อสารง่าย, เข้ากับสถาปัตยกรรม repo, และโชว์พลังของระบบได้ดีที่สุดบน EVE-NG.
