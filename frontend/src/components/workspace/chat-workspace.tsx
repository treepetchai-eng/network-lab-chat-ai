"use client";

import dynamic from "next/dynamic";
import { startTransition, useEffect, useMemo, useState } from "react";
import { ConversationPanel } from "@/components/workspace/conversation-panel";
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

const DeferredMascotPanel = dynamic(
  () => import("@/components/workspace/mascot-panel").then((mod) => mod.MascotPanel),
  {
    ssr: false,
    loading: () => <MascotPanelPlaceholder />,
  },
);

function MascotPanelPlaceholder() {
  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-[32px] border border-white/10 bg-[linear-gradient(180deg,rgba(9,16,29,0.9),rgba(7,12,22,0.72))] px-5 pb-6 pt-5 shadow-[0_30px_80px_rgba(3,8,18,0.45)] backdrop-blur-2xl">
      <div className="absolute inset-x-8 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(125,211,252,0.45),transparent)]" />
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(56,189,248,0.12),transparent_40%),radial-gradient(circle_at_bottom,rgba(6,182,212,0.06),transparent_30%)]" />
      <div className="relative z-10 flex items-center justify-center pb-2">
        <span className="rounded-full border border-cyan-300/14 bg-cyan-400/8 px-3 py-1 text-[0.6rem] font-medium uppercase tracking-[0.2em] text-cyan-200/70">
          Ready
        </span>
      </div>
      <div className="relative z-10 flex min-h-0 flex-1 items-center justify-center py-3">
        <div className="w-full rounded-[28px] border border-white/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.035),rgba(255,255,255,0.018))] p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.05)]">
          <div className="flex aspect-[4/5] items-center justify-center rounded-[24px] border border-white/6 bg-[radial-gradient(circle_at_top,rgba(34,211,238,0.08),transparent_38%),rgba(255,255,255,0.015)]">
            <div className="space-y-3 text-center">
              <div className="mx-auto h-20 w-20 rounded-full border border-cyan-300/14 bg-cyan-400/8 shadow-[0_0_40px_rgba(34,211,238,0.08)]" />
              <div className="space-y-2">
                <div className="mx-auto h-2.5 w-28 rounded-full bg-white/8" />
                <div className="mx-auto h-2.5 w-20 rounded-full bg-cyan-400/10" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ChatWorkspace({ messages, isStreaming, currentStatus, statusHistory, currentProgress, currentSteps, streamingTokens, pendingFinalContent, onStreamingComplete, onSuggestion, composer, isUserTyping }: ChatWorkspaceProps) {
  const [shouldRenderMascot, setShouldRenderMascot] = useState(false);

  useEffect(() => {
    let timeoutId: number | null = null;
    let idleId: number | null = null;
    const browserWindow = window as Window & {
      requestIdleCallback?: (callback: IdleRequestCallback, options?: IdleRequestOptions) => number;
      cancelIdleCallback?: (handle: number) => void;
    };

    if (!browserWindow.matchMedia("(min-width: 1024px)").matches) {
      return;
    }

    const revealMascot = () => {
      startTransition(() => {
        setShouldRenderMascot(true);
      });
    };

    if (browserWindow.requestIdleCallback) {
      idleId = browserWindow.requestIdleCallback(revealMascot, { timeout: 1200 });
    } else {
      timeoutId = browserWindow.setTimeout(revealMascot, 240);
    }

    return () => {
      if (idleId !== null && browserWindow.cancelIdleCallback) {
        browserWindow.cancelIdleCallback(idleId);
      }
      if (timeoutId !== null) {
        browserWindow.clearTimeout(timeoutId);
      }
    };
  }, []);

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
          {shouldRenderMascot ? (
            <DeferredMascotPanel
              isStreaming={isStreaming}
              phase={phase}
            />
          ) : (
            <MascotPanelPlaceholder />
          )}
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
