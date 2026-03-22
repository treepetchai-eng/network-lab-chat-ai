# Backend

FastAPI backend for the chat-only Network Copilot lab.

## What It Does

- Creates chat sessions
- Streams LLM output over SSE
- Runs a single `LLM-first` free-run graph
- Grounds devices with inventory tools before CLI execution
- Executes read-only SSH commands on lab devices

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
│   │   ├── ssh.py
│   │   ├── ssh_compact.py
│   │   └── ssh_synthesis.py
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
FREE_RUN_MAX_PARALLEL_RUN_CLI=8
SSH_CONN_TIMEOUT=10
SSH_READ_TIMEOUT=20
SSH_DIAG_READ_TIMEOUT=60
SSH_DIAG_LAST_READ=1.0
SSH_CONN_IDLE_TTL=45
```

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

## Notes

- The device cache starts empty per session and is populated only after grounding.
- The backend stays `LLM-first`: the model chooses tools, commands, and the final answer.
