"use client";

import { useCallback, useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { useChat } from "@/hooks/use-chat";
import { useSession } from "@/hooks/use-session";
import { AppShell } from "@/components/workspace/app-shell";
import { ChatWorkspace } from "@/components/workspace/chat-workspace";
import { LoadingSkeleton } from "@/components/workspace/loading-skeleton";
import { StickyComposer } from "@/components/workspace/sticky-composer";
import { TopHeader } from "@/components/workspace/top-header";

export function ChatContainer() {
  const [isUserTyping, setIsUserTyping] = useState(false);
  const { sessionId, isLoading: sessionLoading, resetSession } = useSession();
  const {
    messages,
    isStreaming,
    currentStatus,
    statusHistory,
    currentProgress,
    currentSteps,
    streamingTokens,
    pendingFinalContent,
    error,
    setSessionId: setChatSessionId,
    sendMessage,
    resetChat,
    commitFinalize,
  } = useChat();

  useEffect(() => {
    if (sessionId) {
      setChatSessionId(sessionId);
    }
  }, [sessionId, setChatSessionId]);

  const handleSend = useCallback(
    (text: string) => {
      if (!sessionId) {
        return;
      }
      setIsUserTyping(false);
      sendMessage(sessionId, text);
    },
    [sendMessage, sessionId],
  );

  const handleNewChat = useCallback(async () => {
    resetChat();
    const newId = await resetSession();
    if (newId) {
      setChatSessionId(newId);
    }
  }, [resetChat, resetSession, setChatSessionId]);

  const handleRetry = useCallback(async () => {
    const newId = await resetSession();
    if (newId) {
      setChatSessionId(newId);
    }
  }, [resetSession, setChatSessionId]);

  if (sessionLoading) {
    return (
      <div className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-[#04070d] px-6">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(34,211,238,0.15),transparent_32%),linear-gradient(180deg,#040812_0%,#07111e_42%,#04070d_100%)]" />
        <div className="relative w-full max-w-xl rounded-[32px] border border-white/10 bg-[linear-gradient(180deg,rgba(8,13,24,0.92),rgba(6,10,20,0.78))] p-8 shadow-[0_30px_80px_rgba(1,6,18,0.6)] backdrop-blur-2xl">
          <div className="mb-6 text-center">
            <p className="text-[0.7rem] uppercase tracking-[0.28em] text-cyan-100/70">Initializing session</p>
            <h2 className="mt-4 text-2xl font-semibold text-white">Preparing your premium AI workspace</h2>
          </div>
          <LoadingSkeleton lines={5} />
        </div>
      </div>
    );
  }

  if (!sessionId) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-[#04070d] px-6">
        <div className="w-full max-w-lg rounded-[28px] border border-rose-400/22 bg-rose-500/10 px-6 py-6 text-center text-sm text-rose-100 shadow-[0_20px_60px_rgba(244,63,94,0.18)] backdrop-blur-xl">
          <p className="text-base font-medium text-white">Failed to create a session</p>
          <p className="mt-2 leading-6 text-rose-100/80">
            The frontend could not reach the backend on port 8000. Start the backend or retry after it comes back.
          </p>
          <button
            onClick={handleRetry}
            className="mt-5 inline-flex items-center gap-2 rounded-2xl border border-white/12 bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white transition hover:bg-white/[0.08]"
          >
            <RotateCcw className="h-4 w-4" />
            Retry session
          </button>
        </div>
      </div>
    );
  }

  const hasMessages = messages.length > 0;

  return (
    <AppShell
      header={<TopHeader onNewChat={handleNewChat} hasMessages={hasMessages} />}
      error={error}
    >
      <ChatWorkspace
        messages={messages}
        isStreaming={isStreaming}
        currentStatus={currentStatus}
        statusHistory={statusHistory}
        currentProgress={currentProgress}
        currentSteps={currentSteps}
        streamingTokens={streamingTokens}
        pendingFinalContent={pendingFinalContent}
        onStreamingComplete={commitFinalize}
        onSuggestion={handleSend}
        isUserTyping={isUserTyping}
        composer={<StickyComposer onSend={handleSend} disabled={isStreaming} onDraftPresenceChange={setIsUserTyping} />}
      />
    </AppShell>
  );
}
