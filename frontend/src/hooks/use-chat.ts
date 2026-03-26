"use client";
import { startTransition, useCallback, useEffect, useReducer, useRef } from "react";
import { parseSSELine } from "@/lib/sse-parser";
import { SSE_EVENTS } from "@/lib/constants";
import { sendMessageStream } from "@/lib/api";
import type {
  ChatState, ChatAction, ChatMessage, StepData,
  StatusEvent, ToolResultEvent,
  AnalystTokenEvent, AnalystDoneEvent, ErrorEvent,
} from "@/lib/types";

function uid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const initialState: ChatState = {
  sessionId: null,
  messages: [],
  isStreaming: false,
  currentStatus: null,
  statusHistory: [],
  currentProgress: null,
  currentSteps: [],
  streamingTokens: "",
  pendingFinalContent: null,
  error: null,
};

function sameProgress(a: ChatState["currentProgress"], b: ChatState["currentProgress"]) {
  if (a === b) {
    return true;
  }
  if (!a || !b) {
    return false;
  }
  return a.current === b.current && a.total === b.total;
}

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "SET_SESSION":
      if (state.sessionId === action.sessionId) {
        return state;
      }
      return { ...state, sessionId: action.sessionId };
    case "ADD_USER_MESSAGE":
      return { ...state, messages: [...state.messages, action.message] };
    case "SET_STREAMING":
      if (state.isStreaming === action.isStreaming) {
        return state;
      }
      return { ...state, isStreaming: action.isStreaming };
    case "SET_STATUS":
      if (!action.text) {
        const nextProgress = action.progress ?? null;
        if (state.currentStatus === null && sameProgress(state.currentProgress, nextProgress)) {
          return state;
        }
        return { ...state, currentStatus: null, currentProgress: nextProgress };
      }
      if (state.currentStatus === action.text) {
        const nextProgress = action.progress ?? state.currentProgress;
        if (sameProgress(state.currentProgress, nextProgress)) {
          return state;
        }
        return {
          ...state,
          currentProgress: nextProgress,
        };
      }
      return {
        ...state,
        currentStatus: action.text,
        currentProgress: action.progress ?? state.currentProgress,
        statusHistory: [...state.statusHistory, action.text].slice(-6),
      };
    case "ADD_STEP":
      return { ...state, currentSteps: [...state.currentSteps, action.step] };
    case "APPEND_TOKEN":
      if (!action.token) {
        return state;
      }
      return { ...state, streamingTokens: state.streamingTokens + action.token };
    case "FINALIZE_ASSISTANT": {
      // When tokens were already streamed live, the animation is already
      // in progress.  We just mark the final content as pending and let
      // StreamingText catch up.  When NO tokens were streamed yet (e.g.
      // fast-path greetings), we also seed streamingTokens so the
      // animation has something to display.
      //
      // IMPORTANT: if streamingTokens already ≈ full content (within 90%)
      // we skip the animation entirely and commit immediately — this
      // avoids the "progress bar gone → blank → text appears" gap.
      const existingLen = state.streamingTokens.length;
      const finalLen = action.content.length;
      const mostlyStreamed = existingLen > 0 && existingLen >= finalLen * 0.9;

      if (mostlyStreamed) {
        // Already streamed live — commit immediately, no animation gap
        const assistantMsg: ChatMessage = {
          id: uid(),
          role: "assistant",
          content: action.content,
          steps: state.currentSteps,
          timestamp: Date.now(),
        };
        return {
          ...state,
          messages: [...state.messages, assistantMsg],
          currentSteps: [],
          streamingTokens: "",
          pendingFinalContent: null,
          currentStatus: null,
          statusHistory: [],
          currentProgress: null,
          isStreaming: false,
        };
      }

      // Tokens haven't been streamed yet — let StreamingText animate them.
      return {
        ...state,
        pendingFinalContent: action.content,
        streamingTokens: action.content,
      };
    }
    case "COMMIT_FINALIZE": {
      if (!state.pendingFinalContent) return state;
      const assistantMsg: ChatMessage = {
        id: uid(),
        role: "assistant",
        content: state.pendingFinalContent,
        steps: state.currentSteps,
        timestamp: Date.now(),
      };
      return {
        ...state,
        messages: [...state.messages, assistantMsg],
        currentSteps: [],
        streamingTokens: "",
        pendingFinalContent: null,
        currentStatus: null,
        statusHistory: [],
        currentProgress: null,
        isStreaming: false,
      };
    }
    case "FINALIZE_ORPHAN": {
      if (!state.isStreaming && state.currentSteps.length === 0 && !state.streamingTokens) {
        return state;
      }
      const hasContent = state.streamingTokens || state.currentSteps.length > 0;
      if (!hasContent) {
        return { ...state, isStreaming: false, currentStatus: null, statusHistory: [], currentProgress: null, pendingFinalContent: null };
      }
      const orphanMsg: ChatMessage = {
        id: uid(),
        role: "assistant",
        content: state.streamingTokens || "Connection interrupted. Please try again.",
        steps: state.currentSteps,
        timestamp: Date.now(),
      };
      return {
        ...state,
        messages: [...state.messages, orphanMsg],
        currentSteps: [],
        streamingTokens: "",
        pendingFinalContent: null,
        currentStatus: null,
        statusHistory: [],
        currentProgress: null,
        isStreaming: false,
      };
    }
    case "SET_ERROR":
      if (state.error === action.error && (!action.error || state.isStreaming === false)) {
        return state;
      }
      return {
        ...state,
        error: action.error,
        ...(action.error ? { isStreaming: false } : {}),
      };
    case "RESET_CHAT":
      return { ...initialState, sessionId: state.sessionId, pendingFinalContent: null };
    default:
      return state;
  }
}

export function useChat() {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const abortRef = useRef<AbortController | null>(null);
  const isSendingRef = useRef(false);
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tokenBufferRef = useRef("");
  const tokenFlushTimerRef = useRef<number | null>(null);

  const flushBufferedTokens = useCallback(() => {
    if (tokenFlushTimerRef.current !== null) {
      window.clearTimeout(tokenFlushTimerRef.current);
      tokenFlushTimerRef.current = null;
    }

    if (!tokenBufferRef.current) {
      return;
    }

    const tokenChunk = tokenBufferRef.current;
    tokenBufferRef.current = "";

    startTransition(() => {
      dispatch({ type: "APPEND_TOKEN", token: tokenChunk });
    });
  }, []);

  const scheduleTokenFlush = useCallback(() => {
    if (tokenFlushTimerRef.current !== null) {
      return;
    }

    tokenFlushTimerRef.current = window.setTimeout(() => {
      flushBufferedTokens();
    }, 32);
  }, [flushBufferedTokens]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
      if (tokenFlushTimerRef.current !== null) {
        window.clearTimeout(tokenFlushTimerRef.current);
      }
    };
  }, []);

  const setSessionId = useCallback((id: string) => {
    dispatch({ type: "SET_SESSION", sessionId: id });
  }, []);

  const sendMessage = useCallback(async (sessionId: string, content: string) => {
    if (isSendingRef.current) return;
    isSendingRef.current = true;

    abortRef.current?.abort();
    tokenBufferRef.current = "";
    if (tokenFlushTimerRef.current !== null) {
      window.clearTimeout(tokenFlushTimerRef.current);
      tokenFlushTimerRef.current = null;
    }

    const userMsg: ChatMessage = {
      id: uid(),
      role: "user",
      content,
      steps: [],
      timestamp: Date.now(),
    };
    dispatch({ type: "ADD_USER_MESSAGE", message: userMsg });
    dispatch({ type: "SET_ERROR", error: null });
    dispatch({ type: "SET_STREAMING", isStreaming: true });

    const controller = new AbortController();
    abortRef.current = controller;

    let receivedDone = false;

    try {
      const response = await sendMessageStream(sessionId, content, controller.signal);

      if (!response.body) {
        throw new Error("Streaming response body was empty");
      }

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
            case SSE_EVENTS.ROUTING:
              break;
            case SSE_EVENTS.STATUS: {
              const d = evt.data as StatusEvent;
              const args = d.args ?? {};
              const current = typeof args.current === "number" ? args.current : null;
              const total = typeof args.total === "number" ? args.total : null;
              dispatch({
                type: "SET_STATUS",
                text: d.text,
                progress: current !== null && total !== null ? { current, total } : null,
              });
              break;
            }
            case SSE_EVENTS.TOOL_RESULT: {
              const d = evt.data as ToolResultEvent;
              const step: StepData = {
                id: uid(),
                name: d.step_name,
                content: d.content,
                toolName: d.tool_name,
                isError: d.is_error,
              };
              dispatch({ type: "ADD_STEP", step });
              break;
            }
            case SSE_EVENTS.ANALYST_TOKEN: {
              const d = evt.data as AnalystTokenEvent;
              dispatch({ type: "SET_STATUS", text: null });
              tokenBufferRef.current += d.token;
              scheduleTokenFlush();
              break;
            }
            case SSE_EVENTS.ANALYST_DONE: {
              flushBufferedTokens();
              const d = evt.data as AnalystDoneEvent;
              dispatch({ type: "FINALIZE_ASSISTANT", content: d.full_content });
              receivedDone = true;
              // Safety: if StreamingText animation never calls commitFinalize
              // (e.g. component unmounted), auto-commit after 5s.
              // When the reducer already committed (mostlyStreamed path),
              // COMMIT_FINALIZE is a harmless no-op.
              if (safetyTimerRef.current) clearTimeout(safetyTimerRef.current);
              safetyTimerRef.current = setTimeout(() => {
                dispatch({ type: "COMMIT_FINALIZE" });
                safetyTimerRef.current = null;
              }, 5000);
              break;
            }
            case SSE_EVENTS.ERROR: {
              const d = evt.data as ErrorEvent;
              flushBufferedTokens();
              dispatch({ type: "SET_ERROR", error: d.message });
              break;
            }
            case SSE_EVENTS.DONE:
              flushBufferedTokens();
              receivedDone = true;
              dispatch({ type: "SET_STREAMING", isStreaming: false });
              break;
          }
        }
      }
    } catch (err) {
      flushBufferedTokens();
      if (err instanceof Error && err.name !== "AbortError") {
        dispatch({ type: "SET_ERROR", error: err.message });
      }
    } finally {
      flushBufferedTokens();
      if (!receivedDone) {
        dispatch({ type: "FINALIZE_ORPHAN" });
      }
      isSendingRef.current = false;
    }
  }, [flushBufferedTokens, scheduleTokenFlush]);

  const resetChat = useCallback(() => {
    abortRef.current?.abort();
    tokenBufferRef.current = "";
    if (tokenFlushTimerRef.current !== null) {
      window.clearTimeout(tokenFlushTimerRef.current);
      tokenFlushTimerRef.current = null;
    }
    isSendingRef.current = false;
    dispatch({ type: "RESET_CHAT" });
  }, []);

  const commitFinalize = useCallback(() => {
    if (safetyTimerRef.current) {
      clearTimeout(safetyTimerRef.current);
      safetyTimerRef.current = null;
    }
    dispatch({ type: "COMMIT_FINALIZE" });
  }, []);

  return {
    ...state,
    setSessionId,
    sendMessage,
    resetChat,
    commitFinalize,
  };
}
