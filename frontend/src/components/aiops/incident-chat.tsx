"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  AlertCircle, Loader2, MessageSquare, RotateCcw, Send, Server,
} from "lucide-react";
import { createIncidentSession, deleteSession, sendMessageStream } from "@/lib/api";
import { parseSSELine } from "@/lib/sse-parser";
import { SSE_EVENTS } from "@/lib/constants";
import { AssistantMessage } from "@/components/chat/assistant-message";
import { UserMessage } from "@/components/chat/user-message";
import { CollapsibleStep } from "@/components/stream/collapsible-step";
import { MarkdownRenderer } from "@/components/markdown/markdown-renderer";
import type { AIOpsIncidentDetailPayload } from "@/lib/aiops-types";

/* ─────────────────── Types ─────────────────── */

interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  steps: StepItem[];
  status?: string;
}

interface StepItem {
  id: string;
  name: string;
  content: string;
  toolName: string;
  isError: boolean;
}

type State = {
  messages: ChatMsg[];
  streaming: boolean;
  streamingTokens: string;
  currentSteps: StepItem[];
  currentStatus: string | null;
  pendingContent: string | null;
  error: string | null;
};

type Action =
  | { type: "ADD_USER"; msg: ChatMsg }
  | { type: "SET_STREAMING"; v: boolean }
  | { type: "APPEND_TOKEN"; t: string }
  | { type: "ADD_STEP"; step: StepItem }
  | { type: "SET_STATUS"; text: string | null }
  | { type: "FINALIZE"; content: string }
  | { type: "COMMIT" }
  | { type: "SET_ERROR"; error: string | null }
  | { type: "ORPHAN" };

function uid(): string {
  return crypto.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

const init: State = {
  messages: [], streaming: false, streamingTokens: "",
  currentSteps: [], currentStatus: null, pendingContent: null, error: null,
};

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "ADD_USER":
      return { ...s, messages: [...s.messages, a.msg], error: null };
    case "SET_STREAMING":
      return { ...s, streaming: a.v };
    case "APPEND_TOKEN":
      return { ...s, streamingTokens: s.streamingTokens + a.t };
    case "ADD_STEP":
      return { ...s, currentSteps: [...s.currentSteps, a.step] };
    case "SET_STATUS":
      return { ...s, currentStatus: a.text };
    case "FINALIZE": {
      const mostly = s.streamingTokens.length >= a.content.length * 0.9;
      if (mostly) {
        return {
          ...s,
          messages: [...s.messages, { id: uid(), role: "assistant", content: a.content, steps: s.currentSteps }],
          currentSteps: [], streamingTokens: "", pendingContent: null,
          currentStatus: null, streaming: false,
        };
      }
      return { ...s, pendingContent: a.content, streamingTokens: a.content };
    }
    case "COMMIT": {
      if (!s.pendingContent) return s;
      return {
        ...s,
        messages: [...s.messages, { id: uid(), role: "assistant", content: s.pendingContent, steps: s.currentSteps }],
        currentSteps: [], streamingTokens: "", pendingContent: null,
        currentStatus: null, streaming: false,
      };
    }
    case "ORPHAN": {
      if (!s.streaming && !s.streamingTokens && s.currentSteps.length === 0) return s;
      const content = s.streamingTokens || "Connection interrupted.";
      return {
        ...s,
        messages: [...s.messages, { id: uid(), role: "assistant", content, steps: s.currentSteps }],
        currentSteps: [], streamingTokens: "", pendingContent: null,
        currentStatus: null, streaming: false,
      };
    }
    case "SET_ERROR":
      return { ...s, error: a.error, streaming: a.error ? false : s.streaming };
    default:
      return s;
  }
}

/* ─────────────────── Sub-components ─────────────────── */

function StreamingBubble({ tokens, steps, status }: { tokens: string; steps: StepItem[]; status: string | null }) {
  return (
    <div className="flex items-start gap-2 sm:gap-3">
      <div className="mt-1 flex h-7 w-7 sm:h-8 sm:w-8 shrink-0 items-center justify-center rounded-full border border-cyan-300/18 bg-[linear-gradient(135deg,rgba(8,42,64,0.95),rgba(12,132,176,0.82))] shadow-[0_0_18px_rgba(34,211,238,0.16)]">
        <Loader2 className="h-3 w-3 sm:h-3.5 sm:w-3.5 animate-spin text-cyan-200" />
      </div>
      <div className="min-w-0 flex-1 space-y-2 sm:space-y-3">
        <span className="text-[0.7rem] sm:text-[0.74rem] font-medium text-cyan-200/60">Network Copilot</span>
        {steps.length > 0 && (
          <div className="rounded-2xl sm:rounded-[20px] border border-white/10 bg-[linear-gradient(180deg,rgba(9,16,27,0.72),rgba(8,13,23,0.58))] p-1 sm:p-1.5 shadow-[0_12px_34px_rgba(2,7,18,0.16)]">
            {steps.map((s) => (
              <CollapsibleStep key={s.id} step={s} />
            ))}
          </div>
        )}
        {status && !tokens && (
          <div className="flex items-center gap-2 text-[0.76rem] text-slate-500">
            <Loader2 className="h-3 w-3 animate-spin text-cyan-500/70" />
            {status}
          </div>
        )}
        {tokens && (
          <div className="ui-copy text-slate-200">
            <MarkdownRenderer content={tokens} />
            <span className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-cyan-400 align-middle" />
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────── Context banner ─────────────────── */

function ContextBanner({ data }: { data: AIOpsIncidentDetailPayload }) {
  const { incident } = data;
  return (
    <div className="flex items-center gap-3 border-b border-white/[0.06] px-4 py-2.5">
      <Server className="h-3.5 w-3.5 shrink-0 text-cyan-500/70" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[0.74rem] font-semibold text-slate-300">
          {incident.primary_hostname ?? incident.primary_source_ip}
        </p>
        <p className="truncate text-[0.65rem] text-slate-600">
          {incident.incident_no} · {incident.event_family} · device context pre-loaded
        </p>
      </div>
    </div>
  );
}

/* ─────────────────── Main component ─────────────────── */

export function IncidentChat({ data }: { data: AIOpsIncidentDetailPayload }) {
  const [state, dispatch] = useReducer(reducer, init);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const isSendingRef = useRef(false);
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const incidentNo = data.incident.incident_no;

  // Create incident session on mount, cleanup on unmount
  useEffect(() => {
    let destroyed = false;
    let sid: string | null = null;

    createIncidentSession(incidentNo)
      .then((id) => {
        if (destroyed) {
          deleteSession(id, { keepalive: true });
          return;
        }
        sid = id;
        setSessionId(id);
      })
      .catch((e) => {
        if (!destroyed) setSessionError(e instanceof Error ? e.message : "Failed to connect");
      });

    return () => {
      destroyed = true;
      abortRef.current?.abort();
      if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
      if (sid) deleteSession(sid, { keepalive: true });
    };
  }, [incidentNo]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [state.messages, state.streamingTokens]);

  const send = useCallback(async (text: string) => {
    if (!sessionId || isSendingRef.current || !text.trim()) return;
    isSendingRef.current = true;
    setDraft("");

    dispatch({ type: "ADD_USER", msg: { id: uid(), role: "user", content: text, steps: [] } });
    dispatch({ type: "SET_STREAMING", v: true });
    dispatch({ type: "SET_ERROR", error: null });

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    let receivedDone = false;
    try {
      const response = await sendMessageStream(sessionId, text, controller.signal);
      if (!response.body) throw new Error("Empty response body");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remaining } = parseSSELine(buffer);
        buffer = remaining;

        for (const evt of events) {
          switch (evt.event) {
            case SSE_EVENTS.STATUS:
              dispatch({ type: "SET_STATUS", text: (evt.data as { text: string }).text ?? null });
              break;
            case SSE_EVENTS.TOOL_RESULT: {
              const d = evt.data as { step_name: string; content: string; tool_name: string; is_error: boolean };
              dispatch({ type: "ADD_STEP", step: { id: uid(), name: d.step_name, content: d.content, toolName: d.tool_name, isError: d.is_error } });
              break;
            }
            case SSE_EVENTS.ANALYST_TOKEN:
              dispatch({ type: "SET_STATUS", text: null });
              dispatch({ type: "APPEND_TOKEN", t: (evt.data as { token: string }).token });
              break;
            case SSE_EVENTS.ANALYST_DONE:
              dispatch({ type: "FINALIZE", content: (evt.data as { full_content: string }).full_content });
              receivedDone = true;
              if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
              safetyTimerRef.current = setTimeout(() => {
                dispatch({ type: "COMMIT" });
                safetyTimerRef.current = null;
              }, 5000);
              break;
            case SSE_EVENTS.ERROR:
              dispatch({ type: "SET_ERROR", error: (evt.data as { message: string }).message });
              break;
            case SSE_EVENTS.DONE:
              receivedDone = true;
              dispatch({ type: "SET_STREAMING", v: false });
              break;
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name !== "AbortError") {
        dispatch({ type: "SET_ERROR", error: err.message });
      }
    } finally {
      if (!receivedDone) dispatch({ type: "ORPHAN" });
      isSendingRef.current = false;
    }
  }, [sessionId]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(draft);
    }
  }, [send, draft]);

  /* ── Session loading / error ── */
  if (sessionError) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertCircle className="h-6 w-6 text-rose-400/70" />
        <p className="text-[0.82rem] text-slate-400">Could not start chat session</p>
        <p className="text-[0.73rem] text-slate-600">{sessionError}</p>
        <button
          onClick={() => { setSessionError(null); setSessionId(null); createIncidentSession(incidentNo).then(setSessionId).catch(e => setSessionError(e.message)); }}
          className="mt-1 inline-flex items-center gap-1.5 rounded border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[0.76rem] text-slate-400 hover:text-slate-200"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Retry
        </button>
      </div>
    );
  }

  if (!sessionId) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-2 text-center">
        <Loader2 className="h-5 w-5 animate-spin text-cyan-500/70" />
        <p className="text-[0.8rem] text-slate-500">Preparing incident session…</p>
        <p className="text-[0.72rem] text-slate-700">Loading device context for {data.incident.primary_hostname ?? data.incident.primary_source_ip}</p>
      </div>
    );
  }

  const { messages, streaming, streamingTokens, currentSteps, currentStatus, error } = state;
  const isEmpty = messages.length === 0 && !streaming;

  return (
    <div className="flex h-[600px] flex-col overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
      {/* Context banner */}
      <ContextBanner data={data} />

      {/* Messages */}
      <div className="incident-chat flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {isEmpty && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <MessageSquare className="h-7 w-7 text-slate-700" />
            <div>
              <p className="text-[0.84rem] font-medium text-slate-300">Ask about this incident</p>
              <p className="mt-1 text-[0.74rem] text-slate-600">
                Device context and syslog evidence are pre-loaded.<br />
                I can SSH in and run commands for you.
              </p>
            </div>
            <div className="mt-2 flex flex-wrap justify-center gap-2">
              {[
                "What's the current interface status?",
                "Show me the routing table",
                "Check OSPF neighbor state",
              ].map(s => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded border border-white/[0.07] bg-white/[0.025] px-3 py-1.5 text-[0.74rem] text-slate-500 transition hover:border-white/[0.1] hover:text-slate-300"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map(msg => (
          <div key={msg.id}>
            {msg.role === "user" ? (
              <UserMessage content={msg.content} />
            ) : (
              <AssistantMessage message={{ ...msg, timestamp: 0 }} />
            )}
          </div>
        ))}

        {streaming && (
          <StreamingBubble
            tokens={streamingTokens}
            steps={currentSteps}
            status={currentStatus}
          />
        )}

        {error && (
          <div className="flex items-start gap-2 rounded border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2.5">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
            <p className="text-[0.78rem] text-rose-300">{error}</p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Composer */}
      <div className="border-t border-white/[0.06] p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={streaming}
            placeholder="Ask about this incident… (Enter to send, Shift+Enter for newline)"
            rows={1}
            className="flex-1 resize-none overflow-hidden rounded border border-white/[0.07] bg-white/[0.03] px-3 py-2 text-[0.82rem] text-slate-200 placeholder:text-slate-700 focus:border-cyan-500/30 focus:outline-none focus:ring-0 disabled:opacity-50"
            style={{ minHeight: "2.25rem", maxHeight: "6rem" }}
            onInput={e => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 96)}px`;
            }}
          />
          <button
            onClick={() => send(draft)}
            disabled={!draft.trim() || streaming}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-cyan-500/25 bg-cyan-500/[0.08] text-cyan-300 transition hover:bg-cyan-500/15 disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {streaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </div>
  );
}
