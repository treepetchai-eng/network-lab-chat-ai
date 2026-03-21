# Backend

FastAPI backend for the Network Copilot lab.

## What It Does

- Creates chat sessions
- Streams LLM output over SSE
- Runs an `LLM-first` free-run graph
- Grounds devices with inventory tools before CLI execution
- Executes read-only SSH commands on lab devices

## Current Architecture

The backend now uses a single `free_run_agent` flow:

1. User message enters the graph
2. LLM decides whether to call `lookup_device` or `list_all_devices`
3. LLM decides which `run_cli(host, command)` call to make
4. Tool results are fed back to the same LLM
5. The final answer is streamed to the frontend

Runtime guardrails still remain for:

- read-only command safety
- duplicate command blocking
- terminal SSH failure blocking
- session memory and grounded device cache

## Key Files

```text
backend/
├── inventory/
│   └── inventory.csv
├── src/
│   ├── api.py
│   ├── session_manager.py
│   ├── sse_stream.py
│   ├── formatters.py
│   ├── graph/
│   │   ├── builder.py
│   │   ├── state.py
│   │   └── agents/
│   │       └── free_run_agent.py
│   ├── prompts/
│   │   └── ssh.py
│   └── tools/
│       ├── cli_tool.py
│       ├── inventory_tools.py
│       ├── safety.py
│       └── ssh_executor.py
└── requirements.txt
```

## Setup

```bash
cd /home/treepetch/network-lab-chat-ai/backend
pip3 install -r requirements.txt --user
```

Create `backend/.env` with at least:

```env
ROUTER_USER=admin
ROUTER_PASS=admin1234
LLM_PROVIDER=ollama
LLM_MODEL=qwen3.5:9b
LLM_BASE_URL=http://100.96.111.98:11434
```

Optional tuning:

```env
LLM_TOOL_MODEL=
LLM_ANSWER_MODEL=
LLM_API_KEY=
LLM_NUM_CTX=65536
LLM_NUM_PREDICT=1536
LLM_ANSWER_NUM_PREDICT=8192
LLM_MAX_TOKENS=
LLM_ANSWER_MAX_TOKENS=
SSH_CONTEXT_CHAR_BUDGET=180000
SSH_SYNTHESIS_CONTEXT_CHAR_BUDGET=12000
FREE_RUN_MAX_ITERATIONS=8
FREE_RUN_MAX_ITERATIONS_TROUBLESHOOT=20
FREE_RUN_MAX_PARALLEL_RUN_CLI=8
SSH_CONN_TIMEOUT=10
SSH_READ_TIMEOUT=20
SSH_DIAG_READ_TIMEOUT=60
SSH_DIAG_LAST_READ=1.0
SSH_CONN_IDLE_TTL=45
```

Ops platform variables:

```env
DATABASE_URL=postgresql+psycopg://network_ops:admin1234@100.118.96.126:5432/network_ops_ai
SYSLOG_HOST=100.93.135.57
SYSLOG_PORT=22
SYSLOG_USER=treepetch
SYSLOG_PASS=gilardino01
SYSLOG_ROOT=/data/syslog
SYSLOG_INITIAL_SYNC=1
SYSLOG_SYNC_INTERVAL_SECONDS=900
SYSLOG_INGEST_TOKEN=replace_with_shared_collector_token
```

Recommended syslog mode for this lab:

- `syslog-ng` pushes each incoming event to `POST /api/ops/ingest/syslog`
- backend stores raw + normalized + correlated incident data immediately
- polling from the syslog archive remains enabled as a low-frequency fallback

Provider examples:

```env
# Local via Ollama
LLM_PROVIDER=ollama
LLM_MODEL=qwen3.5:9b
LLM_BASE_URL=http://100.96.111.98:11434
```

```env
# ChatGPT / OpenAI
LLM_PROVIDER=openai
LLM_MODEL=your-openai-model
LLM_API_KEY=your_openai_key
```

```env
# Claude / Anthropic
LLM_PROVIDER=anthropic
LLM_MODEL=your-anthropic-model
LLM_API_KEY=your_anthropic_key
```

```env
# Gemini / Google AI
LLM_PROVIDER=gemini
LLM_MODEL=your-gemini-model
LLM_API_KEY=your_google_key
```

```env
# Local or hosted OpenAI-compatible endpoint
LLM_PROVIDER=openai_compatible
LLM_MODEL=qwen2.5-14b-instruct
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=not-needed
```

Supported aliases for `LLM_PROVIDER` include `chatgpt`, `claude`, `gemini`,
`ollama`, and `openai_compatible`. For local non-Ollama servers such as
LM Studio, vLLM, or LocalAI, use `openai_compatible`.

## Run

```bash
cd /home/treepetch/network-lab-chat-ai/backend
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

## API

- `POST /api/session`
- `DELETE /api/session/{session_id}`
- `GET /api/session/{session_id}/validate`
- `POST /api/chat`
- `GET /api/health`
- `GET /api/inventory`

Ops platform endpoints:

- `GET /api/ops/overview`
- `POST /api/ops/sync/inventory`
- `POST /api/ops/sync/syslog`
- `POST /api/ops/ingest/syslog`
- `GET /api/ops/devices`
- `GET /api/ops/events`
- `GET /api/ops/incidents`
- `GET /api/ops/incidents/{id}`
- `POST /api/ops/incidents/{id}/investigate`
- `GET /api/ops/jobs`
- `GET /api/ops/approvals`
- `POST /api/ops/approvals`
- `POST /api/ops/approvals/{id}/approve`
- `POST /api/ops/approvals/{id}/reject`
- `POST /api/ops/approvals/{id}/execute`

## Notes

- The device cache starts empty per session and is populated only after grounding.
- The backend is optimized for the current lab-focused `free_run` mode, not the old supervisor-based graph.
