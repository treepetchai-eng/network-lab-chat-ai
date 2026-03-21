"use client";

import { useMemo } from "react";
import { ConversationPanel } from "@/components/workspace/conversation-panel";
import { MascotPanel } from "@/components/workspace/mascot-panel";
import type { ChatMessage, ProgressState, StepData } from "@/lib/types";
import type { ReactNode } from "react";

interface ChatWorkspaceProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  currentStatus: string | null;
  statusHistory: string[];
  currentProgress: ProgressState | null;
  currentSteps: StepData[];
  streamingTokens: string;
  pendingFinalContent: string | null;
  onStreamingComplete: () => void;
  onSuggestion?: (text: string) => void;
  composer: ReactNode;
  isUserTyping: boolean;
}

export function ChatWorkspace({ messages, isStreaming, currentStatus, statusHistory, currentProgress, currentSteps, streamingTokens, pendingFinalContent, onStreamingComplete, onSuggestion, composer, isUserTyping }: ChatWorkspaceProps) {
  const phase = useMemo<"idle" | "listening" | "grounding" | "planning" | "executing" | "summarizing">(() => {
    const status = (currentStatus ?? "").toLowerCase();
    if (!isStreaming && isUserTyping) return "listening";
    if (!isStreaming && !status) return "idle";

    // Grounding: inventory lookup
    if (status.includes("looking up") || status.includes("loading full device inventory") || status.includes("resolving target")) {
      return "grounding";
    }
    // Planning: initial analysis
    if (status.includes("planning") || status.includes("understanding request") || status.includes("analyzing request")) {
      return "planning";
    }
    // Executing: running CLI commands on devices
    if (status.includes("running") || status.includes("connecting") || status.includes("collected") || status.includes("cli")) {
      return "executing";
    }
    // Summarizing: reviewing, synthesizing, verifying
    if (status.includes("summarizing") || status.includes("reviewing") || status.includes("analyzing")
      || status.includes("synthesizing") || status.includes("verifying") || status.includes("polishing")) {
      return "summarizing";
    }

    // Fallback: if streaming but no recognizable status, assume planning
    return isStreaming ? "planning" : "idle";
  }, [currentStatus, isStreaming, isUserTyping]);

  return (
    <div className="h-full min-h-0 overflow-hidden">
      <div className="grid h-full min-h-0 gap-3 sm:gap-5 lg:grid-cols-[17rem_minmax(0,1fr)] xl:grid-cols-[19rem_minmax(0,1fr)] 2xl:grid-cols-[21rem_minmax(0,1fr)]">
        <div className="hidden min-h-0 overflow-hidden lg:block">
          <MascotPanel
            isStreaming={isStreaming}
            phase={phase}
          />
        </div>
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] gap-3 sm:gap-4">
          <div className="min-h-0 overflow-hidden rounded-2xl sm:rounded-[34px] border border-white/8 bg-[linear-gradient(180deg,rgba(9,16,29,0.78),rgba(7,12,22,0.58))] shadow-[0_28px_80px_rgba(2,7,18,0.34)] backdrop-blur-2xl">
            <ConversationPanel
              messages={messages}
              isStreaming={isStreaming}
              currentStatus={currentStatus}
              statusHistory={statusHistory}
              currentProgress={currentProgress}
              phase={phase}
              currentSteps={currentSteps}
              streamingTokens={streamingTokens}
              pendingFinalContent={pendingFinalContent}
              onStreamingComplete={onStreamingComplete}
              onSuggestion={onSuggestion}
            />
          </div>
          <div className="relative">
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-28 bg-[linear-gradient(180deg,transparent,rgba(4,7,13,0.74)_42%,rgba(4,7,13,0.98))]" />
            <div className="relative">{composer}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
