# AIOps Roadmap — Production Readiness & Human Replacement

> Last updated: 2026-03-24
> Goal: ระบบสามารถแทน NOC engineer ได้ 80%+ โดยที่คนเข้ามาแค่ตัดสินใจ high-risk remediation และ review รายงานประจำวัน

---

## Architecture Philosophy

```
┌─────────────────────────────────────────────────────────────┐
│                  Network Management System (NMS)            │
│         Zabbix / PRTG / LibreNMS / Prometheus+Alertmanager  │
│                                                             │
│  ICMP probe │ SNMP poll │ Threshold alert │ Uptime track    │
│  (ให้ NMS ทำ — มันเก่งเรื่องนี้อยู่แล้ว)                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ Webhook / API push
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    AIOps Engine (ระบบนี้)                    │
│                                                             │
│  รับ event → Correlate → LLM Analyze → Remediate → Resolve  │
│  (เก่งเรื่อง context, correlation, remediation, chat)        │
└─────────────────────────────────────────────────────────────┘
```

**หลักการ:** NMS = sensor layer (รู้ว่ามีอะไรผิดปกติ)
AIOps = brain layer (รู้ว่าต้องทำอะไร และทำให้อัตโนมัติ)

ไม่ build ICMP/SNMP เอง — ใช้ NMS ที่ battle-tested อยู่แล้ว แล้ว integrate ผ่าน webhook/API

---

## สถานะปัจจุบัน (Baseline)

| Layer | มีแล้ว | ขาด |
|---|---|---|
| Metrics/Probe | — (delegate ให้ NMS) | NMS integration (webhook receiver) |
| Log Ingestion | Syslog-ng → PostgreSQL | — |
| Incident Detection | Pattern match + LLM correlation | NMS alert → incident pipeline |
| Troubleshoot | AI SSH evidence collection | Topology-aware RCA |
| Remediation | Approval gate + auto-execute | Pre-approved low-risk playbook |
| Recovery | Syslog recovery signal + AI verify | NMS recovery signal |
| Notification | Slack/log only | LINE, Telegram, Email, escalation chain |
| Observability | Incident dashboard | SLA/uptime, MTTR, trending |
| Self-health | Circuit breaker | Watchdog / dead man's switch |

---

## Phase 1 — NMS Integration (แทน ICMP/SNMP ที่จะ build เอง) 🔴

> ต่อ pipe จาก NMS เข้า AIOps แทนที่จะ build probe เอง

---

### 1.1 Zabbix Webhook → Incident Pipeline

**เป้าหมาย:** Zabbix ส่ง alert เข้า AIOps ทันทีที่ trigger fire แทนที่จะรอ syslog

**วิธี setup ฝั่ง Zabbix:**
1. Administration → Media types → Create media type ประเภท Webhook
2. ตั้ง URL: `http://<aiops-host>/api/aiops/ingest/zabbix`
3. กำหนด parameters ที่ส่งมา:

```json
{
  "event_id":       "{EVENT.ID}",
  "event_name":     "{EVENT.NAME}",
  "host":           "{HOST.NAME}",
  "host_ip":        "{HOST.IP}",
  "trigger_name":   "{TRIGGER.NAME}",
  "trigger_id":     "{TRIGGER.ID}",
  "severity":       "{EVENT.SEVERITY}",
  "status":         "{EVENT.STATUS}",
  "value":          "{EVENT.VALUE}",
  "item_name":      "{ITEM.NAME}",
  "item_value":     "{ITEM.LASTVALUE}",
  "acknowledged":   "{EVENT.ACK.STATUS}",
  "event_time":     "{EVENT.TIME}",
  "event_date":     "{EVENT.DATE}",
  "recovery_value": "{EVENT.RECOVERY.VALUE}"
}
```

**Zabbix severity → AIOps severity mapping:**
```
Zabbix          AIOps
─────────────────────
Disaster    →   critical
High        →   critical
Average     →   warning
Warning     →   warning
Information →   info
Not classified → info
```

**Status mapping:**
```
EVENT.VALUE = 1  (PROBLEM)  → สร้าง / update incident
EVENT.VALUE = 0  (RECOVERY) → trigger recovery pipeline
```

**Backend ที่ต้องเพิ่ม:**
- `POST /api/aiops/ingest/zabbix` — รับ payload, validate, normalize เป็น `NormalizedEvent`
- `zabbix_normalizer.py` — แปลง Zabbix field → AIOps event format
- ส่งต่อเข้า `correlate_event()` เหมือน syslog pipeline ปกติ

**Table ใหม่:** `nms_alerts (id, source, zabbix_event_id, raw_payload_json, normalized_event_id, received_at)`

```
Impact: ได้ ICMP reachability + SNMP threshold + interface down ฟรี จาก Zabbix
Effort: ต่ำ (~1-2 วัน)
```

---

### 1.2 Zabbix API Pull (Metric Context สำหรับ LLM)

**เป้าหมาย:** ตอน AI troubleshoot → ดึง metric ย้อนหลังจาก Zabbix มาให้ LLM วิเคราะห์
แทนที่จะ SSH ไปดู `show interface` เพียงอย่างเดียว

**Zabbix API (JSON-RPC 2.0):**
```
POST http://<zabbix>/api_jsonrpc.php
Auth: API token (Zabbix 5.4+) หรือ user.login
```

**Methods ที่ใช้:**
```python
# หา host ID จาก hostname/IP
host.get(filter={"host": hostname})

# ดึง item ของ host (CPU, memory, interface bps)
item.get(hostids=[host_id], search={"key_": "net.if"})

# ดึง historical values ย้อนหลัง N ชั่วโมง
history.get(itemids=[item_id], time_from=epoch_start, time_till=epoch_now)

# ดึง active problems ของ host
problem.get(hostids=[host_id])

# ดึง trigger ที่ fire ใน timeframe
trigger.get(hostids=[host_id], lastChangeSince=epoch)
```

**Items สำคัญที่ต้อง query:**
```
system.cpu.util          → CPU utilization %
vm.memory.utilization    → Memory %
net.if.in[{#IFNAME}]    → Interface RX bps
net.if.out[{#IFNAME}]   → Interface TX bps
net.if.errors[{#IFNAME}] → Interface error rate
icmpping                 → Reachability (0/1)
icmppingsec              → Latency (ms)
```

**LangChain tool ใหม่ใน `db_tools.py`:**
```python
@tool
def get_zabbix_metrics(device: str, metric: str, hours_back: int = 2) -> str:
    """Query Zabbix for historical metrics of a network device.

    device: hostname or IP address
    metric: cpu | memory | interface_rx | interface_tx | ping_latency | errors
    hours_back: how many hours of history to retrieve (max 24)

    Returns time-series data showing metric values over the period.
    Use this before SSH to understand if the problem is trending or sudden.
    """
```

**ผลที่ LLM จะเห็น:**
```
[Zabbix Metrics] R1 — CPU utilization (last 2h):
  10:00  22%
  10:10  24%
  10:20  31%
  10:30  67%   ← เริ่มสูงขึ้น
  10:40  94%   ← spike
  10:50  96%
  Current: 95% (threshold: 85%)
→ LLM สรุปได้ว่า CPU spike เริ่มก่อน syslog alert 20 นาที
```

**Config ใน .env:**
```
ZABBIX_URL=http://zabbix.internal
ZABBIX_API_TOKEN=your_token_here
ZABBIX_VERIFY_SSL=false
```

```
Impact: LLM มี metric trend → RCA แม่นขึ้นมาก ลด false SSH calls
Effort: กลาง (~2-3 วัน)
```

---

### 1.3 Config Compliance & Drift Detection

**ปัญหา:** มีคนเข้าไปเปลี่ยน config โดยไม่ผ่าน approval → ไม่มีใครรู้
**แนวทาง:**
- Snapshot config ทุก 1 ชั่วโมง (`show running-config`) — ต่อจาก evidence layer ที่มีอยู่
- Diff กับ golden baseline ที่ lock ไว้
- มี unauthorized change → incident `config_drift` + แสดง diff ใน dashboard
- Option: auto-rollback (ต้องมี approval เสมอ)

```
Impact: รู้ทันที ถ้ามี unauthorized change หรือ human error
Effort: ต่ำ (~1-2 วัน ต่อจาก evidence layer)
```

---

### 1.4 Alert Storm Suppression (Topology-aware)

**ปัญหา:** R1 ตาย → NMS ส่ง alert ของ SW1–SW10 พร้อมกัน → เกิด 10 incidents ที่จริงๆ เป็น root cause เดียว

**แนวทาง:**
- เก็บ LLDP/CDP neighbor data จาก snapshot → สร้าง upstream/downstream graph
- เมื่อ incident เกิด → check ว่ามี upstream incident active อยู่หรือไม่
- ถ้าใช่ → suppress / link เป็น child incident ของ root cause
- Root cause resolve → auto-check children

**Table ใหม่:** `network_topology (device_id, neighbor_device_id, local_if, remote_if, protocol, updated_at)`

```
Impact: ลด alert noise 70-80% ในกรณี upstream failure
Effort: กลาง (~3-4 วัน)
```

---

## Phase 2 — Reduce Human Touchpoints 🟡

---

### 2.1 Pre-approved Auto-remediation Playbook

**ปัญหา:** ทุก action รอ approval แม้แต่ low-risk action

**Auto-approve rules:**
```
action_type           risk_level   auto_approve   condition
──────────────────────────────────────────────────────────
clear arp cache       low          ✅ yes          always
clear ip bgp soft     low          ✅ yes          always
shut/no shut iface    medium       ✅ yes          flap > 5/hr
static route add      medium       ⛔ no           always manual
BGP config change     high         ⛔ no           always manual
ACL modification      high         ⛔ no           always manual
```

**Config:** `OPS_AUTO_APPROVE_LOW_RISK=1` ใน .env
ทุก auto-approve ยังคง log ใน `incident_state_history` ครบ

```
Impact: ลด MTTR สำหรับ known patterns ได้ 60-70%
Effort: กลาง (~2-3 วัน)
```

---

### 2.2 Multi-channel Notification & Escalation Chain

**Notification matrix:**
```
Severity    Channel                   SLA ack    Escalate
─────────────────────────────────────────────────────────
P1/Critical  LINE + Telegram + Email   5 นาที    โทรหา on-call
P2/High      LINE + Email             15 นาที    LINE on-call
P3/Warning   Email                    1 ชม.      dashboard
P4/Info      Dashboard เท่านั้น         —          —
```

**Channels:**
- LINE Notify / LINE Bot (ง่ายสุด สำหรับ team ไทย)
- Telegram Bot (`python-telegram-bot`)
- Email (SMTP / SendGrid)
- Webhook generic (รองรับ PagerDuty, OpsGenie)

**Escalation:** ถ้าไม่ acknowledge ใน SLA window → escalate tier ถัดไปอัตโนมัติ

```
Impact: ปลุกคนได้จริงแม้ไม่ได้นั่งดู dashboard
Effort: ต่ำ (~2-3 วัน)
```

---

### 2.3 Maintenance Window

**แนวทาง:**
- API: `POST /api/aiops/maintenance` → `{ devices[], start_at, end_at, reason, created_by }`
- Incident จาก device ใน window → `status = suppressed` ไม่ส่ง notification
- Auto-expire เมื่อครบ window
- Dashboard แสดง maintenance window ที่ active

**Table ใหม่:** `maintenance_windows (id, devices_json, start_at, end_at, reason, created_by)`

```
Impact: ลด false positive ช่วง maintenance เป็น 0
Effort: ต่ำ (~1 วัน)
```

---

### 2.4 Scheduled Proactive Health Check

- **Silent device check** (ทุก 5 นาที): device ที่ไม่มี syslog/NMS alert มา > 10 นาที → AI SSH verify
- **Daily health sweep** (ทุก 06:00): run health profile ทุก device → store snapshot
- **Weekly trend report** (ทุกวันจันทร์ 08:00): summary อีเมล — top 5 incidents, MTTR, hot devices

```
Impact: จับ "silent fail" ที่ NMS/syslog ไม่รายงาน
Effort: ต่ำ (ต่อจาก periodic task framework ที่มี)
```

---

## Phase 3 — Intelligence & Observability 🟢

---

### 3.1 SLA / Uptime Tracking

**Metrics ที่ควรเก็บ:**
- Device availability % (30d / 90d)
- Interface availability % per link (จาก NMS)
- MTTD per incident category
- MTTR per incident category
- Auto-resolve rate vs manual-resolve rate
- False positive rate

**Output:** Tab "SLA" ใน dashboard + PDF export รายเดือน

---

### 3.2 Trending & Predictive Alerting

ใช้ metric history จาก NMS:
- Interface utilization trend → alert ก่อนถึง 80% capacity
- Device restart history → ถ้า restart > 3 ครั้ง/30 วัน → `hardware_degradation` alert
- Error counter trend → CRC errors เพิ่ม → แจ้งก่อน interface ตาย

**Algorithm:** Linear regression บน time-series (ไม่ต้องใช้ ML ซับซ้อน)

---

### 3.3 Topology-aware RCA (Full Graph)

- Pull LLDP/CDP neighbor table จากทุก device → full network graph
- LLM เห็น topology จริง: "R1 คือ upstream ของ SW1, SW2, SW3"
- Impact radius: "ถ้า R1 ตาย → กระทบ X devices, Y services"

---

### 3.4 Incident Knowledge Base

- ทุก incident ที่ resolve → extract `{ pattern, root_cause, solution, success_rate }`
- ครั้งต่อไปที่เจอ pattern เดิม → propose known solution ทันที
- Confidence score เพิ่มขึ้นตาม historical success rate
- ระบบ "เก่งขึ้น" ตามเวลา

---

## Phase 4 — System Reliability 🔵

---

### 4.1 AIOps Self-health Monitor (Dead Man's Switch)

**ปัญหา:** ถ้า ops_loop crash → monitoring ไม่ทำงาน แต่ไม่มีใครรู้

- Loop emit heartbeat ทุก 60s → `system_health` table
- Watchdog process แยก: ถ้าไม่เห็น heartbeat ใน 3 นาที → alert ออกภายนอก
- Health endpoint: `GET /api/aiops/health` → Prometheus/Grafana scrape ได้

```json
{
  "loop_alive": true,
  "last_heartbeat": "2026-03-24T10:00:00Z",
  "nms_connected": true,
  "db_connected": true,
  "llm_reachable": true,
  "open_incidents": 2
}
```

---

### 4.2 LLM Quality Guard

- JSON schema validation ก่อนใช้ LLM output
- Retry 1 ครั้งถ้า invalid JSON
- Fallback → `ESCALATED` + log raw output เพื่อ debug
- Track % invalid JSON per day เพื่อ monitor LLM quality

---

### 4.3 Audit Trail & Compliance Export

ทุก action ต้องตอบได้:
- ใคร/อะไร สั่ง (human / AI / auto)
- เมื่อไหร่ และทำอะไร (exact command)
- ผลลัพธ์ และ rollback plan

Export: PDF report สำหรับ audit / PDF summary รายเดือน
Retention: 1 ปีขึ้นไป

---

## Summary Table

| # | Feature | Phase | Impact | Effort | หมายเหตุ |
|---|---|---|---|---|---|
| 1.1 | Zabbix Webhook → Incident | 1 | ⭐⭐⭐⭐⭐ | 🟢 ต่ำ | แทน ICMP/SNMP build เอง |
| 1.2 | Zabbix API Metric Pull | 1 | ⭐⭐⭐⭐ | 🟡 กลาง | LLM มี metric trend context |
| 1.3 | Config Drift Detection | 1 | ⭐⭐⭐⭐ | 🟢 ต่ำ | ต่อจาก evidence layer |
| 1.4 | Alert Storm Suppression | 1 | ⭐⭐⭐⭐ | 🟡 กลาง | ต้องมี topology data |
| 2.1 | Pre-approved Playbook | 2 | ⭐⭐⭐⭐ | 🟡 กลาง | ใช้ risk_level ที่มีอยู่ |
| 2.2 | Multi-channel Notify | 2 | ⭐⭐⭐⭐⭐ | 🟢 ต่ำ | LINE สำคัญสุด |
| 2.3 | Maintenance Window | 2 | ⭐⭐⭐ | 🟢 ต่ำ | — |
| 2.4 | Proactive Health Check | 2 | ⭐⭐⭐ | 🟢 ต่ำ | ต่อจาก periodic tasks |
| 3.1 | SLA / Uptime Tracking | 3 | ⭐⭐⭐ | 🟡 กลาง | ใช้ NMS data |
| 3.2 | Trending & Predictive | 3 | ⭐⭐⭐⭐ | 🔴 สูง | ต้องมี metric history |
| 3.3 | Full Topology Graph | 3 | ⭐⭐⭐⭐ | 🔴 สูง | LLDP/CDP pull |
| 3.4 | Knowledge Base | 3 | ⭐⭐⭐⭐ | 🟡 กลาง | ใช้ incident history |
| 4.1 | Self-health Watchdog | 4 | ⭐⭐⭐⭐⭐ | 🟢 ต่ำ | — |
| 4.2 | LLM Quality Guard | 4 | ⭐⭐⭐ | 🟢 ต่ำ | — |
| 4.3 | Audit Trail Export | 4 | ⭐⭐⭐ | 🟡 กลาง | — |

---

## Human Replacement Estimate

```
ปัจจุบัน (Baseline):         ~35% auto
หลัง Phase 1 (NMS + drift):  ~58% auto
หลัง Phase 2 (notify + auto-approve): ~74% auto
หลัง Phase 3 (intelligence): ~83% auto
หลัง Phase 4 (reliability):  ~86% auto

คนยังต้องทำ (~14%):
  - Approve high-risk remediation
  - Review weekly trend report
  - Handle physical/hardware failure
  - Tune threshold และ playbook rules
  - ดูแล NMS configuration
```

---

## Quick Wins (ทำได้ภายใน 1 สัปดาห์ — เพิ่ม 30%+ automation)

1. **Zabbix Webhook Receiver** — endpoint รับ alert จาก Zabbix เข้า incident pipeline
2. **LINE Notify** — ~50 lines ใน `ops_loop.py` แจ้ง P1/P2 ได้ทันที
3. **Maintenance Window** — table + API + check ใน `correlate_event()`
4. **Config Drift** — ต่อจาก `evidence.py` + daily diff job
5. **AIOps Watchdog** — process แยกที่ check heartbeat + alert ออกภายนอก

> NMS: **Zabbix** (confirmed)
> เมื่อ implement จริง ต้องรู้ Zabbix version (5.x / 6.x / 7.x) เพราะ API token auth เริ่มใน 5.4
> Zabbix version < 5.4 ใช้ `user.login` แทน API token
