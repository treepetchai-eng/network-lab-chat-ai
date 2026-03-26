"use client";

import { useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { MessageList } from "@/components/chat/message-list";
import { WelcomeScreen } from "@/components/chat/welcome-screen";
import type { ChatMessage, ProgressState, StepData } from "@/lib/types";

interface ConversationPanelProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  currentStatus: string | null;
  statusHistory: string[];
  currentProgress: ProgressState | null;
  phase: "idle" | "listening" | "grounding" | "planning" | "executing" | "summarizing";
  currentSteps: StepData[];
  streamingTokens: string;
  pendingFinalContent: string | null;
  onStreamingComplete: () => void;
  onSuggestion?: (text: string) => void;
}

export function ConversationPanel({ messages, isStreaming, currentStatus, statusHistory, currentProgress, phase, currentSteps, streamingTokens, pendingFinalContent, onStreamingComplete, onSuggestion }: ConversationPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottomRef = useRef(true);
  const scrollRafRef = useRef<number | null>(null);
  const lastStreamScrollAtRef = useRef(0);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;

    const updateStickiness = () => {
      const distanceFromBottom = node.scrollHeight - node.scrollTop - node.clientHeight;
      stickToBottomRef.current = distanceFromBottom < 140;
    };

    updateStickiness();
    node.addEventListener("scroll", updateStickiness, { passive: true });
    return () => {
      node.removeEventListener("scroll", updateStickiness);
    };
  }, []);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node || !stickToBottomRef.current) return;

    if (scrollRafRef.current) {
      cancelAnimationFrame(scrollRafRef.current);
      scrollRafRef.current = null;
    }

    const shouldThrottle = isStreaming && streamingTokens.length > 0;
    const behavior = shouldThrottle ? "auto" : messages.length > 0 ? "smooth" : "auto";

    if (shouldThrottle) {
      const now = performance.now();
      if (now - lastStreamScrollAtRef.current < 80) {
        return;
      }
    }

    scrollRafRef.current = requestAnimationFrame(() => {
      if (shouldThrottle) {
        lastStreamScrollAtRef.current = performance.now();
        node.scrollTop = node.scrollHeight;
      } else {
        node.scrollTo({ top: node.scrollHeight, behavior });
      }
      scrollRafRef.current = null;
    });

    return () => {
      if (scrollRafRef.current) {
        cancelAnimationFrame(scrollRafRef.current);
        scrollRafRef.current = null;
      }
    };
  }, [messages.length, isStreaming, streamingTokens.length, currentSteps.length]);

  return (
    <motion.section
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      {messages.length === 0 && !isStreaming ? (
        <div className="flex-1 overflow-y-auto px-3 py-4 sm:px-5 sm:py-5 md:px-6 md:py-6">
          <WelcomeScreen onSuggestion={onSuggestion} />
        </div>
      ) : (
        <MessageList
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
          scrollRef={scrollRef}
        />
      )}
    </motion.section>
  );
}
