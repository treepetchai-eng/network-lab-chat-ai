"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Loader2,
  MessageSquare,
  RotateCcw,
  Send,
  Server,
} from "lucide-react";
import { AssistantMessage } from "@/components/chat/assistant-message";
import { UserMessage } from "@/components/chat/user-message";
import { StreamingText } from "@/components/stream/streaming-text";
import { CollapsibleStep } from "@/components/stream/collapsible-step";
import { StatusIndicator } from "@/components/stream/status-indicator";
import { useChat } from "@/hooks/use-chat";
import { useIncidentSession } from "@/hooks/use-incident-session";
import type { AIOpsIncidentDetailPayload } from "@/lib/aiops-types";

type Phase = "idle" | "listening" | "grounding" | "planning" | "executing" | "summarizing";

function resolvePhase(currentStatus: string | null, isStreaming: boolean): Phase {
  const status = (currentStatus ?? "").toLowerCase();

  if (!isStreaming && !status) {
    return "idle";
  }
  if (status.includes("looking up") || status.includes("loading full device inventory") || status.includes("resolving target")) {
    return "grounding";
  }
  if (status.includes("planning") || status.includes("understanding request") || status.includes("analyzing request")) {
    return "planning";
  }
  if (status.includes("running") || status.includes("connecting") || status.includes("collected") || status.includes("cli")) {
    return "executing";
  }
  if (
    status.includes("summarizing") ||
    status.includes("reviewing") ||
    status.includes("analyzing") ||
    status.includes("synthesizing") ||
    status.includes("verifying") ||
    status.includes("polishing")
  ) {
    return "summarizing";
  }

  return isStreaming ? "planning" : "idle";
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-2.5 rounded-2xl border border-cyan-300/10 bg-[linear-gradient(180deg,rgba(12,28,44,0.7),rgba(8,18,32,0.6))] px-4 py-3">
      <div className="relative flex h-5 w-5 items-center justify-center">
        <span className="absolute inset-0 rounded-full border border-cyan-300/25 animate-beacon-breathe" />
        <span className="absolute inset-[2px] rounded-full border border-cyan-200/30 border-t-cyan-100/80 animate-beacon-spin" />
        <span className="relative h-1.5 w-1.5 rounded-full bg-cyan-300/70" />
      </div>
      <span className="text-[0.82rem] font-medium text-cyan-100/60">Thinking</span>
      <span className="flex items-center gap-1">
        <span className="h-1 w-1 rounded-full bg-cyan-300/50 animate-thinking-dot" style={{ animationDelay: "0ms" }} />
        <span className="h-1 w-1 rounded-full bg-cyan-300/50 animate-thinking-dot" style={{ animationDelay: "160ms" }} />
        <span className="h-1 w-1 rounded-full bg-cyan-300/50 animate-thinking-dot" style={{ animationDelay: "320ms" }} />
      </span>
    </div>
  );
}

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

interface StreamingBubbleProps {
  currentStatus: string | null;
  statusHistory: string[];
  currentProgress: { current: number; total: number } | null;
  currentSteps: Array<{ id: string; name: string; content: string; toolName: string; isError: boolean }>;
  streamingTokens: string;
  pendingFinalContent: string | null;
  onStreamingComplete: () => void;
  isStreaming: boolean;
}

function StreamingBubble({
  currentStatus,
  statusHistory,
  currentProgress,
  currentSteps,
  streamingTokens,
  pendingFinalContent,
  onStreamingComplete,
  isStreaming,
}: StreamingBubbleProps) {
  const phase = resolvePhase(currentStatus, isStreaming);

  return (
    <div className="flex items-start gap-2 sm:gap-3">
      <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-cyan-300/18 bg-[linear-gradient(135deg,rgba(8,42,64,0.95),rgba(12,132,176,0.82))] shadow-[0_0_18px_rgba(34,211,238,0.16)]">
        <Loader2 className="h-3 w-3 animate-spin text-cyan-200" />
      </div>
      <div className="min-w-0 flex-1 space-y-2 sm:space-y-3">
        <span className="text-[0.7rem] font-medium text-cyan-200/60">Network Copilot</span>

        {currentSteps.length > 0 && (
          <div className="rounded-2xl border border-white/10 bg-[linear-gradient(180deg,rgba(9,16,27,0.72),rgba(8,13,23,0.58))] p-1 shadow-[0_12px_34px_rgba(2,7,18,0.16)]">
            {currentSteps.map((step) => (
              <CollapsibleStep key={step.id} step={step} />
            ))}
          </div>
        )}

        {currentStatus && (
          <StatusIndicator
            text={currentStatus}
            history={statusHistory}
            phase={phase}
            progress={currentProgress}
          />
        )}

        {streamingTokens ? (
          <div className="rounded-2xl border border-white/8 bg-[linear-gradient(180deg,rgba(8,13,22,0.76),rgba(8,15,24,0.54))] px-3 py-3 text-slate-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
            <StreamingText
              tokens={streamingTokens}
              isComplete={!!pendingFinalContent}
              finalContent={pendingFinalContent ?? undefined}
              onAnimationComplete={onStreamingComplete}
            />
          </div>
        ) : !currentStatus ? (
          <ThinkingIndicator />
        ) : null}
      </div>
    </div>
  );
}

export function IncidentChat({ data }: { data: AIOpsIncidentDetailPayload }) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const incidentNo = data.incident.incident_no;

  const { sessionId, isLoading: sessionLoading, error: sessionError, retrySession } = useIncidentSession(incidentNo);
  const {
    messages,
    isStreaming,
    currentStatus,
    statusHistory,
    currentProgress,
    currentSteps,
    streamingTokens,
    pendingFinalContent,
    error: chatError,
    sendMessage,
    resetChat,
    commitFinalize,
  } = useChat();

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: isStreaming && streamingTokens ? "auto" : "smooth",
    });
  }, [messages.length, streamingTokens.length, currentSteps.length, isStreaming, streamingTokens]);

  const handleSend = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!sessionId || !trimmed || isStreaming) {
      return;
    }

    setDraft("");
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    await sendMessage(sessionId, trimmed);
  }, [isStreaming, sendMessage, sessionId]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend(draft);
    }
  }, [draft, handleSend]);

  const handleRetrySession = useCallback(async () => {
    resetChat();
    setDraft("");
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
    await retrySession();
  }, [resetChat, retrySession]);

  if (sessionError && !sessionId) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
        <AlertCircle className="h-6 w-6 text-rose-400/70" />
        <p className="text-[0.82rem] text-slate-400">Could not start chat session</p>
        <p className="text-[0.73rem] text-slate-600">{sessionError}</p>
        <button
          onClick={() => { void handleRetrySession(); }}
          className="mt-1 inline-flex items-center gap-1.5 rounded border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[0.76rem] text-slate-400 hover:text-slate-200"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          Retry
        </button>
      </div>
    );
  }

  if (sessionLoading && !sessionId) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-2 text-center">
        <Loader2 className="h-5 w-5 animate-spin text-cyan-500/70" />
        <p className="text-[0.8rem] text-slate-500">Preparing incident session…</p>
        <p className="text-[0.72rem] text-slate-700">
          Loading device context for {data.incident.primary_hostname ?? data.incident.primary_source_ip}
        </p>
      </div>
    );
  }

  const isEmpty = messages.length === 0 && !isStreaming;

  return (
    <div className="flex h-[600px] flex-col overflow-hidden rounded-lg border border-white/[0.07] bg-[#0c1220]">
      <ContextBanner data={data} />

      <div className="incident-chat flex-1 space-y-4 overflow-y-auto px-4 py-4">
        {isEmpty && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <MessageSquare className="h-7 w-7 text-slate-700" />
            <div>
              <p className="text-[0.84rem] font-medium text-slate-300">Ask about this incident</p>
              <p className="mt-1 text-[0.74rem] text-slate-600">
                Device context and syslog evidence are pre-loaded.
                <br />
                I can SSH in and run commands for you.
              </p>
            </div>
            <div className="mt-2 flex flex-wrap justify-center gap-2">
              {[
                "What's the current interface status?",
                "Show me the routing table",
                "Check OSPF neighbor state",
              ].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => { void handleSend(suggestion); }}
                  className="rounded border border-white/[0.07] bg-white/[0.025] px-3 py-1.5 text-[0.74rem] text-slate-500 transition hover:border-white/[0.1] hover:text-slate-300"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((message) => (
          <div key={message.id}>
            {message.role === "user" ? (
              <UserMessage content={message.content} />
            ) : (
              <AssistantMessage message={message} />
            )}
          </div>
        ))}

        {isStreaming && (
          <StreamingBubble
            currentStatus={currentStatus}
            statusHistory={statusHistory}
            currentProgress={currentProgress}
            currentSteps={currentSteps}
            streamingTokens={streamingTokens}
            pendingFinalContent={pendingFinalContent}
            onStreamingComplete={commitFinalize}
            isStreaming={isStreaming}
          />
        )}

        {chatError && (
          <div className="flex items-start gap-2 rounded border border-rose-500/25 bg-rose-500/[0.06] px-3 py-2.5">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-400" />
            <p className="text-[0.78rem] text-rose-300">{chatError}</p>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="border-t border-white/[0.06] p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!sessionId || isStreaming}
            placeholder="Ask about this incident… (Enter to send, Shift+Enter for newline)"
            rows={1}
            className="flex-1 resize-none overflow-hidden rounded border border-white/[0.07] bg-white/[0.03] px-3 py-2 text-[0.82rem] text-slate-200 placeholder:text-slate-700 focus:border-cyan-500/30 focus:outline-none focus:ring-0 disabled:cursor-not-allowed disabled:opacity-50"
            style={{ minHeight: "2.25rem", maxHeight: "6rem" }}
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 96)}px`;
            }}
          />
          <button
            onClick={() => { void handleSend(draft); }}
            disabled={!sessionId || !draft.trim() || isStreaming}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded border border-cyan-500/25 bg-cyan-500/[0.08] text-cyan-300 transition hover:bg-cyan-500/15 disabled:cursor-not-allowed disabled:opacity-30"
          >
            {isStreaming ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </div>
  );
}
