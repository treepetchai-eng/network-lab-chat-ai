# AIOps Network Incident Management System

## Project Goal
Build a fully automated AIOps system that:
1. Ingests syslog-ng logs in near real-time into PostgreSQL (TimescaleDB)
2. Uses LLM to correlate logs → create incidents with Timeline, RCA, Recommendation
3. Auto-troubleshoot: escalate if physical, remediate if config-fixable
4. Human approval gate before executing remediation
5. Event-driven recovery detection → auto-resolve incident with explanation
6. Full incident state machine: OPEN → REMEDIATING → VERIFYING → RESOLVED

## Environment
- Network devices: Cisco routers/switches on EVE-NG
- Syslog collector: SSH into SYSLOG_HOST, logs at SYSLOG_ROOT
- Database: PostgreSQL at DATABASE_HOST (already running)
- LLM: Ollama at LLM_BASE_URL, model LLM_MODEL
- All credentials are in .env file

## Access Permissions
Claude Code has FULL permission to:
- SSH into syslog server to inspect logs, configure syslog-ng
- Read/write PostgreSQL schema and data
- Create/modify/delete any file in this project
- Install Python packages as needed

## Architecture Required

### 1. Log Ingestion (syslog-ng → PostgreSQL)
- SSH into syslog server, check current syslog-ng config at /etc/syslog-ng/
- Read sample logs from SYSLOG_ROOT to understand format
- Configure syslog-ng to forward logs to PostgreSQL directly OR
  write Python log tailer that reads syslog-ng output and inserts to DB
- Target latency: < 5 seconds from device → DB

### 2. Database Schema (PostgreSQL)
Create these tables if not exist:
- network_logs: raw logs (id, received_at, device_host, severity, 
  facility, raw_message, parsed_fields JSONB, incident_id)
- incidents: (id, incident_id, title, severity P1-P4, status, 
  created_at, resolved_at, rca JSONB, timeline JSONB, 
  recommendation JSONB, affected_devices JSONB, resolution_summary JSONB)
- remediation_plans: (id, incident_id, steps JSONB, status, 
  approved_by, approved_at, executed_at, rollback_commands JSONB)
- incident_state_history: audit trail ทุก state change

### 3. Correlation Engine (Python)
- Poll new logs from DB every 10 seconds
- Rule-based pre-filter: group logs by time-window (5 min) + device proximity
- Patterns to detect: BGP Down/Up, OSPF neighbor, Interface flap, 
  CPU high, Link down/up
- Send ONLY grouped cluster (max 50 lines) to LLM — never raw stream
- LLM output must be structured JSON (incident object)

### 4. Incident Engine
- Create incident with: incident_id, title, severity, timeline[], 
  rca{root_cause, probable_cause, confidence}, recommendation[], affected[]
- Dedup: if similar incident already OPEN, append logs — don't create new
- Trigger Troubleshoot Engine immediately after creation

### 5. Troubleshoot Engine
LLM decides:
- "PHYSICAL" or "UNKNOWN" → set status=ESCALATED, send Slack/log notification
- "CONFIG_FIXABLE" → create remediation_plan with steps[], 
  rollback_commands[], confidence score
- Remediation step types: show_command (read-only), config_push, verify

### 6. Approval Gate + Execution
- Remediation plan waits for status="APPROVED" in DB
- After approval: execute steps via Netmiko SSH to device
- After each step: run verify command, check expected output
- If verify fails: auto rollback immediately
- Auto rollback window: 120 seconds

### 7. Recovery Detection (Event-Driven — NOT LLM polling)
- Separate process watches new logs for recovery patterns:
  "Established", "Interface.*up", "OSPF.*Full", "cleared"
- Match recovery signal against open incidents
- If match found: wake LLM to verify (NOT poll continuously)
- Stable window: wait 5 minutes, check no re-fault before closing
- LLM generates resolution_summary with full timeline + MTTR

### 8. Incident State Machine
States: OPEN → ESCALATED | REMEDIATING → VERIFYING → RESOLVED | RESOLVED_UNCERTAIN | MONITORING
- MONITORING: recovery detected but confidence < 0.85 or flapping
- RESOLVED_UNCERTAIN: resolved but root cause unclear
- All state transitions logged in incident_state_history

### 9. Incident State History & Audit
Every action must be logged:
- who/what triggered the state change
- timestamp
- reason/note from LLM


```

## LLM Guidelines
- Model: use LLM_MODEL from env (qwen3.5:9b)
- Always request JSON output with explicit schema in prompt
- Include /no_think tag in prompts (Qwen3.5 specific) for faster response
- Max context per LLM call: 50 log lines + incident context
- Confidence threshold for auto-close: 0.90
- If LLM returns invalid JSON: retry once, then fallback to ESCALATED

## First Steps for Claude Code
1. SSH into SYSLOG_HOST, run: 
   - cat /etc/syslog-ng/syslog-ng.conf
   - ls SYSLOG_ROOT
   - tail -20 [latest log file]
   Understand current log format before writing any code

2. Connect to DATABASE_HOST, check existing schema:
   \dt — list existing tables
   Understand what's already built

3. Show me what you found, then propose implementation plan
   before writing code

## Important Constraints
- Use async Python (asyncio) throughout — never blocking calls
- Use SQLAlchemy async for all DB operations
- Netmiko calls must be in thread pool (run_in_executor)
- All LLM calls must have timeout=60s + retry logic
- Never hardcode credentials — always from .env
- Every component must have structured logging (structlog or loguru)