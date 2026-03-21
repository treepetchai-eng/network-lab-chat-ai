# Frontend

Next.js frontend for the Network Copilot lab UI.

## What It Does

- Creates and validates backend sessions
- Sends chat messages to the backend SSE endpoint
- Streams assistant text progressively
- Shows tool steps, current status, and animated mascot state
- Renders a focused chat workspace for network operations workflows

## Stack

- Next.js 16
- React 19
- TypeScript
- Tailwind CSS 4
- Framer Motion

## Run

```bash
cd /home/treepetch/network-lab-chat-ai/frontend
npm install
npm run dev
```

The dev server runs on `http://localhost:3001`.

Create `frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Important UI Areas

```text
frontend/src/
├── app/
├── components/
│   ├── chat/
│   ├── markdown/
│   ├── stream/
│   └── workspace/
├── hooks/
└── lib/
```

Key pieces:

- `components/chat/chat-container.tsx` - main chat entry
- `components/workspace/chat-workspace.tsx` - overall workspace layout
- `components/workspace/mascot-panel.tsx` - animated left mascot
- `components/stream/streaming-text.tsx` - progressive streaming text renderer
- `hooks/use-chat.ts` - SSE chat state machine

## Notes

- The current UI is tuned for the LLM-first free-run backend.
- Several older side panels and summary cards were removed to keep the interface cleaner and aligned with the current product direction.
