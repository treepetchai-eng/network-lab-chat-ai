# Network Lab Chat AI

LLM-first network copilot for a lab environment.

This project now follows a simplified architecture:

- `backend/` runs a FastAPI API and a single `free_run_agent`
- `frontend/` runs a Next.js chat UI with live streaming, tool steps, and an animated mascot
- the LLM decides device grounding, command choice, iteration, and final answer
- backend logic is limited to runtime guardrails and tool execution

## Repo Layout

```text
network-lab-chat-ai/
├── backend/
├── frontend/
└── README.md
```

## Quick Start

Backend:

```bash
cd backend
pip3 install -r requirements.txt --user
uvicorn src.api:app --host 0.0.0.0 --port 8000 --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3001`

## Current Design

The project is intentionally `LLM-first`.

The model is responsible for:

- understanding the user request
- deciding whether to ground one device, many devices, or all devices
- choosing when to call inventory tools
- planning and issuing CLI commands
- deciding whether to continue or stop
- writing the final response

The backend is responsible for:

- session lifecycle
- SSE streaming
- read-only command safety
- SSH execution
- lightweight runtime guardrails

## More Details

- `backend/README.md`
- `frontend/README.md`
