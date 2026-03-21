# Project Update

Last updated: 2026-03-18

## 1. Summary

โปรเจกต์นี้ถูกขยายจาก `LLM-first network chat lab` ไปเป็น `AI Network Ops Platform` ที่ยังคงหลักการเดิมคือ:

- หน้า chat เดิมยังทำงานเหมือนเดิมที่ `/`
- LLM ยังเป็นตัวตัดสินใจเรื่อง reasoning, device scope, command choice, evidence gathering, และ final answer
- backend เพิ่มความสามารถฝั่ง operations, storage, incident handling, approval workflow, และ UI console โดยไม่ดึง reasoning หลักออกจาก LLM

ตอนนี้ระบบมี 2 ส่วนหลัก:

- `Chat Workspace` สำหรับคุยกับ AI แบบเดิม
- `Ops Console` ที่ `/ops` สำหรับดู devices, events, incidents, jobs, approvals และทำ approval-gated execution

สถานะปัจจุบัน:

- สำหรับ `lab` นี้ยังไม่มี auth จริง
- ใช้ `Lab Identity` ใน UI เพื่อกำหนด actor name/role สำหรับ policy, approval, execute, และ audit
- execution ถูกจำกัดด้วย policy engine และ command guardrails แม้จะยังไม่มี login จริง

## 2. Architecture Changes

### 2.1 LLM Provider Switching

รองรับการสลับ provider ได้ผ่าน `.env` โดยไม่ผูกกับ Ollama อย่างเดียวอีกแล้ว

รองรับ:

- `ollama`
- `openai` / `chatgpt`
- `anthropic` / `claude`
- `gemini`
- `openai_compatible` / local endpoints เช่น LM Studio หรือ vLLM

แนวทางที่ใช้:

- เพิ่ม LLM factory กลาง
- ย้าย logic การสร้าง model ออกจาก graph builder
- ทำให้ agent ใช้ interface ของ chat model แบบ generic

ผลลัพธ์:

- สลับ provider ได้จาก config
- local LLM และ cloud LLM อยู่ในโครงเดียวกัน

### 2.2 LLM-first Guardrails

มีการย้ำกติกาใน `AGENTS.md` เพื่อคงแนวทาง `LLM-first free-run`

แนวทางที่ยึดไว้:

- backend ไม่เขียน conclusion แทน LLM สำหรับเคสปกติ
- backend ทำหน้าที่ tool execution, evidence preservation, safety, consistency
- หลีกเลี่ยง hardcoded playbook ที่แทน reasoning ของโมเดล

## 3. Backend Work

### 3.1 Chat Runtime

ปรับปรุงส่วน chat เดิมให้เสถียรขึ้นโดยยังคง behavior เดิม

- เพิ่ม SSE error propagation เพื่อไม่ให้หน้า chat ค้างเงียบเมื่อ provider หรืองาน graph ล้ม
- ยืนยันว่า `/api/session`, `/api/chat`, `/api/health` ยังใช้งานได้

### 3.2 Operations Data Layer

เพิ่ม data layer สำหรับ platform operations

องค์ประกอบหลัก:

- PostgreSQL เป็น operational database
- `devices`
- `device_interfaces`
- `raw_logs`
- `syslog_checkpoints`
- `normalized_events`
- `incidents`
- `incident_event_links`
- `incident_history`
- `notification_logs`
- `remediation_tasks`
- `scan_history`
- `jobs`
- `approvals`

แนวคิด:

- `syslog-ng` ยังเป็น raw log collector หลัก
- backend ingest เข้าฐานข้อมูลเพื่อใช้สำหรับ normalize, correlate, query, และให้ AI ใช้เป็น evidence

### 3.3 Inventory Sync

เพิ่ม flow สำหรับ sync inventory จาก `backend/inventory/inventory.csv` เข้า PostgreSQL

ผลลัพธ์:

- inventory ไม่ได้เป็นแค่ CSV อย่างเดียวอีกต่อไป
- หน้า ops ใช้ข้อมูล inventory แบบ structured ได้
- ระบบสามารถ map event เข้ากับ device ได้ดีขึ้น

หมายเหตุ:

- credentials ยังอยู่ที่ `.env`
- ไม่ได้ย้าย user/password ของอุปกรณ์ไปเก็บใน database

### 3.4 Syslog Integration

เพิ่ม syslog ingestion จากเครื่อง `syslog-ng` ที่มีอยู่แล้ว

สิ่งที่ทำ:

- เพิ่ม `push-based ingest` จาก `syslog-ng` เข้า backend โดยตรง
- polling จาก remote collector archive ยังคงอยู่เป็น fallback
- ใช้ checkpoint ตามไฟล์และ offset สำหรับ fallback path
- เก็บ raw log ลง DB
- parse เป็น normalized events
- ผูก event เข้ากับ inventory devices
- ทำให้ ingest ทนต่อ collector format ได้ทั้ง raw event line และ RFC5424-style envelope

event types ที่รองรับมีเช่น:

- interface up/down
- OSPF neighbor up/down
- BGP neighbor up/down
- EIGRP neighbor up/down
- config change
- track up/down
- traceback
- critical region fault
- SSH enabled

### 3.5 Incident Correlation

เพิ่ม incident engine แบบ generic

แนวทาง:

- ใช้ `correlation_key`
- group events ที่สัมพันธ์กัน
- เปิด incident เมื่อเจอ issue events
- resolve incident เมื่อเจอ recovery events

ผลลัพธ์:

- ระบบเริ่มมอง log เป็น incident ไม่ใช่แค่รายการข้อความ

### 3.6 AI Investigation

เพิ่ม flow ให้ incident สามารถถูกสรุปด้วย AI จาก evidence ในระบบได้

สิ่งที่เกิดขึ้น:

- ดึง incident + related events จาก DB
- ให้ LLM สรุปและช่วยวิเคราะห์
- เก็บ `ai_summary` กลับเข้า incident
- เก็บ structured `AI artifacts` แยกจาก incident row เพื่อใช้กับ audit และ UI

### 3.7 Approval and Execution

เพิ่ม approval workflow สำหรับงานที่อาจมีผลต่ออุปกรณ์

รองรับ:

- สร้าง proposal
- approve
- reject
- execute

จุดสำคัญ:

- คำสั่ง destructive บางประเภทถูก block แม้จะ approve แล้ว
- execution ถูกแยกจาก proposal อย่างชัดเจน
- verify commands รองรับหลัง execution

guardrails ที่เพิ่ม:

- ถ้า execute แล้วได้ `[BLOCKED]`, `[TIMEOUT ERROR]`, `[SSH ERROR]`, `[CONFIG ERROR]` จะไม่ถูกนับว่า executed สำเร็จ
- blocked execution ตอบ `409` แทน `500`
- job failure ถูก persist จริงใน DB ไม่หายเพราะ transaction rollback
- approval มี `requested_by_role`, `required_approval_role`, `required_execution_role`
- approval เก็บ `execution_status`, `failure_category`, `decision_comment`, `policy_snapshot`, `evidence_snapshot`
- risk สูงสามารถบังคับ `awaiting_second_approval` ได้

### 3.8 Governance and Audit

ยกระดับจาก guardrails กระจายตาม path ไปเป็น component ชัดขึ้น

สิ่งที่เพิ่ม:

- action catalog แบบ structured
- policy engine กลางสำหรับ role/risk/readiness/blocked commands
- audit trail table แยก
- AI artifact table แยก
- legacy approval rows ถูก serialize แบบ fallback ให้แสดงสถานะได้ถูกแม้ข้อมูลมาจากก่อน schema รุ่นใหม่

สิ่งที่เก็บใน audit/artefact แล้ว:

- actor และ actor role
- action ที่ทำ
- entity ที่เกี่ยวข้อง
- payload snapshot
- provider/model/prompt version
- confidence/readiness/root cause/proposed actions

### 3.9 Device Intelligence

เพิ่ม device-level service ใหม่

สิ่งที่มี:

- `/api/ops/devices/{id}`
- recent events
- recent incidents
- recent jobs
- recent approvals
- last successful checks
- related devices ใน site เดียวกัน
- blast radius summary
- AI summary สำหรับ device ผ่าน `/api/ops/devices/{id}/summarize`

### 3.10 Ops API

เพิ่ม endpoints สำหรับ platform

หลัก ๆ ได้แก่:

- `/api/ops/overview`
- `/api/ops/sites`
- `/api/ops/action-catalog`
- `/api/ops/search`
- `/api/ops/ingest/syslog`
- `/api/ops/devices`
- `/api/ops/devices/{id}`
- `/api/ops/devices/{id}/interfaces`
- `/api/ops/devices/{id}/summarize`
- `/api/ops/events`
- `/api/ops/incidents`
- `/api/ops/incidents/{id}`
- `/api/ops/incidents/{id}/status`
- `/api/ops/incidents/{id}/assign`
- `/api/ops/incidents/{id}/resolve`
- `/api/ops/incidents/{id}/notify`
- `/api/ops/incidents/{id}/remediation-status`
- `/api/ops/incidents/{id}/chat`
- `/api/ops/incidents/{id}/investigate`
- `/api/ops/analyze/site`
- `/api/ops/analyze/device`
- `/api/ops/analyze/global`
- `/api/ops/scan-incidents`
- `/api/ops/jobs`
- `/api/ops/approvals`
- `/api/ops/approvals/{id}/approve`
- `/api/ops/approvals/{id}/reject`
- `/api/ops/approvals/{id}/execute`
- `/api/ops/sync/inventory`
- `/api/ops/sync/syslog`

### 3.11 Database Migrations and Bootstrap

ปรับจากแนว `create_all()` ตรง ๆ ไปเป็น migration-first flow

สิ่งที่ทำ:

- เพิ่ม Alembic config และ initial revision สำหรับ schema ของ ops platform
- ให้ backend bootstrap DB ผ่าน migration เป็นค่าเริ่มต้น
- ถ้าเจอฐานข้อมูลเก่าที่ถูกสร้างไว้ครบแล้วแต่ยังไม่มี `alembic_version` ระบบจะ `stamp` ให้
- ถ้าเจอ schema แบบครึ่ง ๆ กลาง ๆ ระบบจะ fail ชัดเจนแทนการเดาสุ่ม

ผลลัพธ์:

- schema lifecycle คุมได้ดีขึ้น
- upgrade ในอนาคตปลอดภัยกว่า
- ฐานข้อมูลที่ใช้งานอยู่แล้วถูกย้ายเข้า migration flow ได้โดยไม่ต้องทิ้งข้อมูล

### 3.12 Background Sync Runtime

ปรับ sync runtime ให้แยกความรับผิดชอบชัดขึ้น

สิ่งที่ทำ:

- เอา inventory/syslog initial sync ออกจาก blocking startup path
- เพิ่ม embedded scheduler สำหรับ inventory/syslog loops
- เพิ่ม worker entrypoint แยกสำหรับรัน background sync นอก API process
- เพิ่ม guard กัน sync ซ้อนกันระหว่าง manual trigger กับ background loop

ผลลัพธ์:

- API startup เบาขึ้น
- ลดโอกาส job duplicate
- ไปต่อเป็น worker model ได้ง่ายขึ้น

## 4. Frontend Work

### 4.1 Preserve Existing Chat

หน้า chat เดิมยังอยู่ที่ `/`

สิ่งที่รักษาไว้:

- live streaming
- chat session flow
- tool step behavior เดิม

### 4.2 New Ops Console

เพิ่ม route ใหม่ที่ `/ops`

หน้าที่มี:

- `/ops`
- `/ops/analysis`
- `/ops/devices`
- `/ops/events`
- `/ops/history`
- `/ops/incidents`
- `/ops/incidents/[id]`
- `/ops/jobs`
- `/ops/approvals`

### 4.3 Navigation and Layout

ปรับ UI ให้ใช้งานเป็น console มากขึ้น

สิ่งที่เพิ่ม/แก้:

- global navbar ใช้ได้ทั้ง chat และ ops
- sidebar ใน ops
- แก้ scroll behavior ของหน้า ops
- route ย่อยถูก mark active ใน navigation ได้ถูกต้อง
- เพิ่ม `Lab Identity` panel สำหรับ actor name/role แบบไม่ใช้ auth จริง
- เพิ่ม global search ใน sidebar

### 4.4 Usability Improvements

เพิ่มให้แต่ละหน้ามี state สำหรับ:

- loading
- error
- empty state
- refresh

และเพิ่ม filters/search/sort แบบใช้งานจริงมากขึ้น

สิ่งที่ปรับรอบล่าสุด:

- เปลี่ยนหน้าหลัก `devices/events/incidents/jobs/approvals` ให้เป็น table-driven workflow UI
- ย้าย filter/sort/pagination ไป backend query layer
- sync filter state ลง URL
- เพิ่ม confirmation dialog สำหรับ approve/reject/execute
- ปรับ incident detail proposal form ให้เป็น structured sections มากขึ้น
- dashboard `/ops` ถูกยกระดับให้เห็น queue งานจริง เช่น approvals, jobs, incidents

#### Devices

- search
- filter ตาม site
- filter ตาม role
- toggle เฉพาะอุปกรณ์ที่มี open incidents

#### Events

- search
- filter ตาม severity
- filter ตาม event type
- date range filter ตามช่วงเวลาที่ event เกิด
- sort จาก server
- pagination จาก server

#### Incidents

- status chips
- search
- filter ตาม severity
- filter ตาม site
- date range filter ตาม updated time
- sort จาก server
- pagination จาก server

#### Jobs

- search
- filter ตาม status
- filter ตาม job type
- sort จาก server
- pagination จาก server

#### Approvals

- search
- filter ตาม status
- filter ตาม risk level
- sort จาก server
- pagination จาก server
- action dialog รองรับ comment และเห็น risk/readiness/required roles

#### Global Search

- ค้นหา device / incident / job / approval จากจุดเดียว
- jump ไปยัง resource ที่เกี่ยวข้องได้ทันที
- ใช้ quick lookup ระหว่าง workflow ได้

ทุกหน้ามี:

- `Clear filters`
- `Showing X of Y`

### 4.5 New Ops Views

เพิ่ม view ใหม่เพื่อให้ workflow ใช้งานจริงขึ้น

- `/ops/analysis`
  - Site / Device / Global analysis
  - ใช้ evidence จาก inventory + events + incidents
  - แสดง summary, root cause, operational impact, runbook steps, suggested commands

- `/ops/history`
  - resolved incidents
  - resolution notes
  - resolved by / resolved at
  - filter/search สำหรับ historical review

- `/ops/devices/[id]`
  - inventory profile
  - observed interfaces
  - blast radius
  - recent incidents/events
  - recent approvals/jobs
  - AI artifacts
  - related devices

- `/ops/incidents/[id]`
  - summary
  - timeline
  - evidence
  - proposals
  - audit
  - lifecycle controls
  - assignment
  - notification log
  - remediation monitor
  - incident-scoped AI chat
  - ใช้ action catalog ตอนสร้าง proposal

- sidebar ของ ops
  - Lab Identity
  - Global Search
  - quick navigation

### 4.6 Timestamps

เพิ่มการแสดงเวลาให้ครบขึ้นและมีความหมายมากขึ้น

สิ่งที่ปรับ:

- format เวลาแบบอ่านง่าย
- relative time
- แสดงเฉพาะเวลาที่มีความหมายกับ object นั้น

สิ่งที่โชว์:

#### Device

- `Last seen`

#### Event

- `Occurred`
- `Ingested`

#### Incident

- `Opened`
- `Updated`
- `Last event`
- `Closed`

#### Job

- `Created`
- `Started`
- `Completed`

#### Approval

- `Requested`
- `Decided`
- `Executed`
- execution outcome ผ่าน `execution_status` และ `failure_category`

## 5. Infra and Environment

### 5.1 Database

ใช้ PostgreSQL เป็น operational store

แนวทาง:

- แยก DB สำหรับ platform นี้
- backend เชื่อมผ่าน `DATABASE_URL`
- schema ถูก track ผ่าน Alembic แล้ว

### 5.2 Syslog

ใช้ `syslog-ng` เครื่องเดิมเป็น collector หลัก

แนวทาง:

- ไม่สร้าง receiver ใหม่ทับของเดิม
- ใช้ `syslog-ng -> backend ingest endpoint` เป็น path หลัก
- polling จาก collector archive ยังคงไว้เป็น fallback

### 5.3 Frontend Dev Setup

ปรับ dev setup ให้เข้าจากหลาย origin ได้ดีขึ้น

สิ่งที่ทำ:

- bind dev server ที่ `0.0.0.0`
- รองรับ `allowedDevOrigins`
- ไม่ hardcode `localhost` สำหรับ API base URL

### 5.4 Lab Identity Instead of Auth

สำหรับตอนนี้ intentionally ยังไม่ทำ auth จริง เพราะ environment เป็น lab

สิ่งที่ใช้แทน:

- actor name
- actor role (`viewer`, `operator`, `approver`, `admin`)
- policy checks ฝั่ง backend
- audit trail ที่เก็บ actor/role ทุก action

ข้อสำคัญ:

- อันนี้ช่วยให้ workflow ทดสอบได้จริง
- แต่ยังไม่ถือเป็น security boundary แบบ production

## 6. Testing and Verification

### 6.1 Automated Checks

ตรวจแล้วผ่าน:

- `backend` test suite
- focused ops tests
- `frontend` lint
- `frontend` production build

ผลล่าสุดที่ยืนยัน:

- backend: `100 passed, 101 skipped`
- focused ops tests: `23 passed`
- frontend lint: pass
- frontend build: pass

### 6.2 Live Validation

มีการทดสอบแบบ live แล้ว เช่น:

- health endpoint
- chat session creation
- chat SSE stream
- ops overview
- paginated/faceted ops endpoints
- date-range filtering for events/incidents
- incident lifecycle updates (`acknowledged`, `in_progress`, `resolved`)
- assignment + notification mock
- incident investigation
- incident chat
- site/device/global analysis
- autonomous incident scan
- device AI summary
- readonly approval execution
- blocked config execution path
- ops pages ตอบ `200`
- migration bootstrap บน Postgres จริง

หมายเหตุ live validation ล่าสุด:

- approval ใหม่แบบ policy-aware ถูก create/approve/execute path จนถึง audit จริง
- AI artifacts ถูกสร้างจริงทั้งระดับ incident และ device
- execution path ล่าสุดตอบ `failed_transport` กับ lab devices ปัจจุบัน เพราะ SSH reachability ณ ตอนทดสอบ ไม่ใช่เพราะ workflow พัง
- incident detail และ device detail pages โหลดข้อมูลจริงได้จาก backend
- analysis page และ history page โหลดจริงได้
- global search และ action catalog endpoint ตอบจริง
- `POST /api/ops/ingest/syslog` ตอบจริงด้วย token
- ทดสอบ `logger -> syslog-ng -> backend -> DB` สำเร็จ และ query event ใหม่ผ่าน `/api/ops/events` ได้
- remediation status แสดง task progress และ failure taxonomy จาก approval ใหม่ได้จริง

## 7. Current Platform State

ตอนนี้ระบบทำได้แล้ว:

- chat กับ LLM แบบเดิม
- สลับ LLM provider ได้
- sync inventory เข้า DB
- ingest syslog จาก collector แบบ push ได้
- มี polling fallback จาก collector archive
- normalize events
- correlate incidents
- update incident lifecycle / assignment / resolution
- ส่ง notification mock และเก็บ log ได้
- ให้ AI ช่วย investigate incident
- incident chat ใน scope ของ incident
- วิเคราะห์แบบ site / device / global ได้
- autonomous scan หา incident ใหม่ได้
- สร้าง approval proposal
- approve/reject/execute
- monitor remediation task status ได้
- แสดงข้อมูลผ่าน ops console
- filter/search ข้อมูลหลักได้
- sort/pagination/filter จาก backend ได้
- ดู timestamps ที่สำคัญได้
- ใช้ date range filter ได้ใน events/incidents
- มี dashboard แบบ workflow queue มากขึ้น
- DB อยู่ภายใต้ migration flow แล้ว
- มี action catalog
- มี policy engine กลาง
- มี policy snapshot / evidence snapshot
- มี audit trail แยก
- มี AI artifacts แยก
- มี lab identity context ใน UI
- มี device detail page
- มี history page
- มี analysis page
- มี global search
- มี incident detail แบบ multi-section
- มี failure taxonomy ที่ชัดขึ้น

## 8. Known Limitations

สิ่งที่ยังตั้งใจไม่ได้ทำหรือยังไม่ครบ:

- ยังไม่มี auth / RBAC จริง
- ใช้ `Lab Identity` แทน auth จริงสำหรับตอนนี้
- config execution ยังไม่ save startup-config อัตโนมัติ
- ยังไม่มี topology visualization
- ยังไม่มี background queue เต็มรูปแบบ เช่น Redis/Celery/Arq
- worker แยก process ทำได้แล้ว แต่ยังไม่ใช่ distributed job system เต็มรูป
- reachability ของ lab devices ยังมีผลต่อ live execute และอาจขึ้น `failed_transport` ได้เมื่อ SSH path ใช้งานไม่ได้
- approval authority ยังเป็น policy-by-role ในเชิง lab ไม่ใช่ real identity / directory-backed authority
- syslog ingest token ยังเป็น shared secret ระหว่าง collector กับ backend ไม่ใช่ secret backend เต็มรูป

## 9. Recommended Next Steps

ลำดับที่คุ้มที่สุดต่อจากนี้:

1. เพิ่ม auth / RBAC
2. เพิ่ม topology / blast-radius visualization
3. เพิ่ม structured change proposal ให้ละเอียดขึ้นอีกขั้น
4. ยกระดับ background jobs ไปเป็น queue/worker เต็มรูป
5. เพิ่ม audit/export/reporting

## 10. Access

- Chat: `http://localhost:3001/`
- Ops Console: `http://localhost:3001/ops`

## 11. Important Notes

- ไม่ควรเก็บ secrets ลง repo หรือเอกสารสรุปนี้
- โครงสร้างทั้งหมดถูกออกแบบให้ยังคงแนว `LLM-first free-run`
- backend เพิ่ม safety, storage, observability, และ workflow แต่ไม่ได้แทน reasoning หลักของโมเดล
