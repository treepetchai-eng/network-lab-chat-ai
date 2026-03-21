"use client";

import { UserMessage } from "./user-message";
import { AssistantMessage } from "./assistant-message";
import { StatusIndicator } from "@/components/stream/status-indicator";
import { CollapsibleStep } from "@/components/stream/collapsible-step";
import { StreamingText } from "@/components/stream/streaming-text";
import { Network } from "lucide-react";
import type { ChatMessage, ProgressState, StepData } from "@/lib/types";
import type { RefObject } from "react";

type Phase = "idle" | "listening" | "grounding" | "planning" | "executing" | "summarizing";

/** Animated "thinking" indicator shown before any tokens arrive. */
function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-2.5 rounded-2xl sm:rounded-[18px] border border-cyan-300/10 bg-[linear-gradient(180deg,rgba(12,28,44,0.7),rgba(8,18,32,0.6))] px-4 py-3 sm:px-5 sm:py-3.5">
      <div className="relative flex h-5 w-5 items-center justify-center">
        <span className="absolute inset-0 rounded-full border border-cyan-300/25 animate-beacon-breathe" />
        <span className="absolute inset-[2px] rounded-full border border-cyan-200/30 border-t-cyan-100/80 animate-beacon-spin" />
        <span className="relative h-1.5 w-1.5 rounded-full bg-cyan-300/70" />
      </div>
      <span className="text-[0.82rem] sm:text-[0.86rem] text-cyan-100/60 font-medium">Thinking</span>
      <span className="flex items-center gap-1">
        <span className="h-1 w-1 rounded-full bg-cyan-300/50 animate-thinking-dot" style={{ animationDelay: "0ms" }} />
        <span className="h-1 w-1 rounded-full bg-cyan-300/50 animate-thinking-dot" style={{ animationDelay: "160ms" }} />
        <span className="h-1 w-1 rounded-full bg-cyan-300/50 animate-thinking-dot" style={{ animationDelay: "320ms" }} />
      </span>
    </div>
  );
}

interface Props {
  messages: ChatMessage[];
  isStreaming: boolean;
  currentStatus: string | null;
  statusHistory: string[];
  currentProgress: ProgressState | null;
  phase: Phase;
  currentSteps: StepData[];
  streamingTokens: string;
  pendingFinalContent: string | null;
  onStreamingComplete: () => void;
  scrollRef: RefObject<HTMLDivElement | null>;
}

export function MessageList({
  messages,
  isStreaming,
  currentStatus,
  statusHistory,
  currentProgress,
  phase,
  currentSteps,
  streamingTokens,
  pendingFinalContent,
  onStreamingComplete,
  scrollRef,
}: Props) {
  return (
    <div ref={scrollRef} className="chat-scroll flex-1 overflow-y-auto px-3 py-3 sm:px-5 sm:py-5 md:px-6 md:py-6">
      <div className="mx-auto flex w-full max-w-[48rem] flex-col gap-5 sm:gap-8 pb-16">
        {messages.map((msg, index) =>
          msg.role === "user" ? (
            <div key={msg.id} className={index >= messages.length - 2 ? "animate-fade-in-up" : ""}>
              <UserMessage content={msg.content} />
            </div>
          ) : (
            <div key={msg.id} className={index >= messages.length - 2 ? "animate-fade-in-up" : ""}>
              <AssistantMessage message={msg} />
            </div>
          ),
        )}

        {isStreaming && (
          <div className="flex items-start gap-2 sm:gap-3 animate-fade-in">
            <div className="mt-1 flex h-7 w-7 sm:h-8 sm:w-8 shrink-0 items-center justify-center rounded-full border border-cyan-300/18 bg-[linear-gradient(135deg,rgba(8,42,64,0.95),rgba(12,132,176,0.82))] shadow-[0_0_18px_rgba(34,211,238,0.16)]">
              <Network className="h-3 w-3 sm:h-3.5 sm:w-3.5 text-white" />
            </div>
            <div className="min-w-0 flex-1 space-y-2 sm:space-y-3">
              {/* Label for streaming assistant */}
              <span className="text-[0.7rem] sm:text-[0.74rem] font-medium text-cyan-200/60">Network Copilot</span>

              {currentSteps.length > 0 && (
                <div className="rounded-2xl sm:rounded-[20px] border border-white/10 bg-[linear-gradient(180deg,rgba(9,16,27,0.72),rgba(8,13,23,0.58))] p-1 sm:p-1.5 shadow-[0_12px_34px_rgba(2,7,18,0.16)]">
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
                <div className="rounded-2xl sm:rounded-[24px] border border-white/8 bg-[linear-gradient(180deg,rgba(8,13,22,0.76),rgba(8,15,24,0.54))] px-3 py-3 sm:px-5 sm:py-4 text-slate-200 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]">
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
        )}
      </div>
    </div>
  );
}
