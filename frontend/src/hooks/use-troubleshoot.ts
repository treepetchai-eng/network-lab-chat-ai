"use client";

import { useCallback, useReducer, useRef } from "react";
import { parseSSELine } from "@/lib/sse-parser";
import { troubleshootPlanSSE, troubleshootExecuteSSE } from "@/lib/ops-api";
import type { LabRole } from "@/lib/ops-types";
import type {
  TroubleshootStep,
  TroubleshootRound,
  TroubleshootState,
} from "@/lib/ops-types";

/* ── Reducer ────────────────────────────────────── */

type Action =
  | { type: "START_PLANNING" }
  | { type: "SET_STATUS"; text: string }
  | { type: "ADD_STEP"; step: TroubleshootStep }
  | { type: "APPEND_TOKEN"; token: string }
  | { type: "PLAN_READY"; sessionId: string; planText: string }
  | { type: "START_EXECUTING" }
  | { type: "ROUND_DONE"; roundNumber: number; analysis: string; artifactId: number | null; approvalId: number | null }
  | { type: "SET_ERROR"; error: string }
  | { type: "RESET" };

const initialState: TroubleshootState = {
  phase: "idle",
  sessionId: null,
  currentPlan: "",
  currentStatus: null,
  streamingTokens: "",
  currentSteps: [],
  rounds: [],
  error: null,
};

function uid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function reducer(state: TroubleshootState, action: Action): TroubleshootState {
  switch (action.type) {
    case "START_PLANNING":
      return { ...state, phase: "planning", error: null, streamingTokens: "", currentStatus: "Creating investigation plan...", currentSteps: [] };
    case "SET_STATUS":
      return { ...state, currentStatus: action.text };
    case "ADD_STEP":
      return { ...state, currentSteps: [...state.currentSteps, action.step] };
    case "APPEND_TOKEN":
      return { ...state, streamingTokens: state.streamingTokens + action.token };
    case "PLAN_READY":
      return { ...state, phase: "plan_ready", sessionId: action.sessionId, currentPlan: action.planText, currentStatus: null, streamingTokens: "" };
    case "START_EXECUTING":
      return { ...state, phase: "executing", error: null, streamingTokens: "", currentStatus: "Executing investigation plan...", currentSteps: [] };
    case "ROUND_DONE": {
      const round: TroubleshootRound = {
        roundNumber: action.roundNumber,
        planText: state.currentPlan,
        steps: state.currentSteps,
        analysisText: action.analysis,
        approvalId: action.approvalId,
        artifactId: action.artifactId,
      };
      return { ...state, phase: "round_done", rounds: [...state.rounds, round], currentStatus: null, streamingTokens: "" };
    }
    case "SET_ERROR":
      return { ...state, phase: "error", error: action.error, currentStatus: null };
    case "RESET":
      return { ...initialState, rounds: state.rounds };
    default:
      return state;
  }
}

/* ── SSE reader ─────────────────────────────────── */

async function readSSE(
  response: Response,
  dispatch: React.Dispatch<Action>,
  extraHandlers?: Record<string, (data: Record<string, unknown>) => void>,
) {
  if (!response.ok) {
    const text = await response.text().catch(() => "Request failed");
    dispatch({ type: "SET_ERROR", error: text });
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    dispatch({ type: "SET_ERROR", error: "No response stream" });
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { events, remaining } = parseSSELine(buffer);
      buffer = remaining;

      for (const evt of events) {
        const data = evt.data as Record<string, unknown>;
        switch (evt.event) {
          case "status":
            if (data.text) dispatch({ type: "SET_STATUS", text: String(data.text) });
            break;
          case "tool_result":
            dispatch({
              type: "ADD_STEP",
              step: {
                id: uid(),
                toolName: String(data.tool_name ?? "tool"),
                stepName: String(data.step_name ?? ""),
                content: String(data.content ?? ""),
                isError: Boolean(data.is_error),
              },
            });
            break;
          case "analyst_token":
            if (data.token) dispatch({ type: "APPEND_TOKEN", token: String(data.token) });
            break;
          case "error":
            dispatch({ type: "SET_ERROR", error: String(data.message ?? "Unknown error") });
            break;
          case "done":
            break;
          default:
            extraHandlers?.[evt.event]?.(data);
            break;
        }
      }
    }
  } catch (err) {
    if ((err as Error).name !== "AbortError") {
      dispatch({ type: "SET_ERROR", error: (err as Error).message ?? "Stream read failed" });
    }
  }
}

/* ── Hook ───────────────────────────────────────── */

export function useTroubleshoot(incidentId: number, actorName: string, actorRole: LabRole) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const abortRef = useRef<AbortController | null>(null);
  const sessionRef = useRef<string | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const startPlan = useCallback(async () => {
    cancel();
    dispatch({ type: "START_PLANNING" });
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      const response = await troubleshootPlanSSE(incidentId, actorName, actorRole, ac.signal);
      await readSSE(response, dispatch, {
        plan_ready: (data) => {
          const sessionId = String(data.session_id ?? "");
          sessionRef.current = sessionId;
          dispatch({ type: "PLAN_READY", sessionId, planText: String(data.plan_text ?? "") });
        },
        analyst_done: () => { /* plan_ready handles it */ },
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        dispatch({ type: "SET_ERROR", error: (err as Error).message ?? "Failed to create plan" });
      }
    }
  }, [incidentId, actorName, actorRole, cancel]);

  const executePlan = useCallback(async (instruction = "") => {
    const sid = sessionRef.current;
    if (!sid) {
      dispatch({ type: "SET_ERROR", error: "No troubleshoot session" });
      return;
    }

    cancel();
    dispatch({ type: "START_EXECUTING" });
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      const response = await troubleshootExecuteSSE(incidentId, sid, instruction, actorName, actorRole, ac.signal);
      await readSSE(response, dispatch, {
        round_done: (data) => {
          dispatch({
            type: "ROUND_DONE",
            roundNumber: Number(data.round_number ?? 1),
            analysis: String(data.analysis ?? ""),
            artifactId: data.artifact_id != null ? Number(data.artifact_id) : null,
            approvalId: data.approval_id != null ? Number(data.approval_id) : null,
          });
        },
        analyst_done: () => { /* round_done handles it */ },
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        dispatch({ type: "SET_ERROR", error: (err as Error).message ?? "Execution failed" });
      }
    }
  }, [incidentId, actorName, actorRole, cancel]);

  const continuePlan = useCallback(async (instruction = "") => {
    // For continuation, we re-enter planning through the same session
    // but with an instruction to propose next steps
    const sid = sessionRef.current;
    if (!sid) {
      dispatch({ type: "SET_ERROR", error: "No troubleshoot session" });
      return;
    }

    cancel();
    dispatch({ type: "START_EXECUTING" });
    const ac = new AbortController();
    abortRef.current = ac;

    const continueInstruction = instruction || "The previous investigation was not conclusive. Propose and execute additional diagnostic steps to narrow down the root cause.";

    try {
      const response = await troubleshootExecuteSSE(incidentId, sid, continueInstruction, actorName, actorRole, ac.signal);
      await readSSE(response, dispatch, {
        round_done: (data) => {
          dispatch({
            type: "ROUND_DONE",
            roundNumber: Number(data.round_number ?? 1),
            analysis: String(data.analysis ?? ""),
            artifactId: data.artifact_id != null ? Number(data.artifact_id) : null,
            approvalId: data.approval_id != null ? Number(data.approval_id) : null,
          });
        },
        analyst_done: () => { /* round_done handles it */ },
      });
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        dispatch({ type: "SET_ERROR", error: (err as Error).message ?? "Continue failed" });
      }
    }
  }, [incidentId, actorName, actorRole, cancel]);

  const reset = useCallback(() => {
    cancel();
    sessionRef.current = null;
    dispatch({ type: "RESET" });
  }, [cancel]);

  return { state, startPlan, executePlan, continuePlan, cancel, reset };
}
