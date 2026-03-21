"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import { parseSSELine } from "@/lib/sse-parser";
import { fetchOpsLoopStatus, opsLoopStreamSSE } from "@/lib/ops-api";
import type { OpsLoopConfig, OpsLoopStage, OpsLoopStatus } from "@/lib/ops-types";

/* ── State ──────────────────────────────────────── */

export interface LiveStep {
  id: string;
  stepName: string;
  content: string;
  isError: boolean;
  toolName: string;
}

interface OpsLoopState {
  connected: boolean;
  currentPhase: string;
  incidentStatus: string;
  stages: OpsLoopStage[];
  latestApprovalId: number | null;
  latestApprovalStatus: string | null;
  config: OpsLoopConfig | null;
  error: string | null;
  terminalState: "success" | "needs_action" | "escalated" | null;
  availableActions: string[];
  escalationContext: { analysis: string; root_cause: string; confidence_score: number; created_at: string | null } | null;
  liveSteps: LiveStep[];
  liveStatus: string | null;
  isTroubleshooting: boolean;
}

type Action =
  | { type: "CONNECTED" }
  | { type: "DISCONNECTED" }
  | { type: "SET_STATUS"; status: OpsLoopStatus }
  | { type: "ADD_STAGE"; stage: OpsLoopStage }
  | { type: "SET_ERROR"; error: string }
  | { type: "TROUBLESHOOT_STARTED" }
  | { type: "TROUBLESHOOT_ENDED" }
  | { type: "ADD_LIVE_STEP"; step: LiveStep }
  | { type: "SET_LIVE_STATUS"; text: string | null };

const initialState: OpsLoopState = {
  connected: false,
  currentPhase: "idle",
  incidentStatus: "new",
  stages: [],
  latestApprovalId: null,
  latestApprovalStatus: null,
  config: null,
  error: null,
  terminalState: null,
  availableActions: [],
  escalationContext: null,
  liveSteps: [],
  liveStatus: null,
  isTroubleshooting: false,
};

let _uid = 0;
function uid() {
  return `live-${++_uid}-${Date.now().toString(36)}`;
}

function reducer(state: OpsLoopState, action: Action): OpsLoopState {
  switch (action.type) {
    case "CONNECTED":
      return { ...state, connected: true, error: null };
    case "DISCONNECTED":
      return { ...state, connected: false };
    case "SET_STATUS": {
      const isTroubleshooting = state.isTroubleshooting || action.status.current_phase === "troubleshooting";
      return {
        ...state,
        currentPhase: action.status.current_phase,
        incidentStatus: action.status.incident_status,
        stages: action.status.stages,
        latestApprovalId: action.status.latest_approval_id,
        latestApprovalStatus: action.status.latest_approval_status,
        config: action.status.config,
        terminalState: action.status.terminal_state ?? null,
        availableActions: action.status.available_actions ?? [],
        escalationContext: action.status.escalation_context ?? null,
        isTroubleshooting,
        liveSteps: state.isTroubleshooting ? state.liveSteps : [],
        liveStatus: state.isTroubleshooting ? state.liveStatus : (isTroubleshooting ? "Troubleshoot in progress..." : null),
      };
    }
    case "ADD_STAGE": {
      const already = state.stages.some(
        (s) => s.stage === action.stage.stage && s.timestamp === action.stage.timestamp
      );
      if (already) return state;
      return { ...state, stages: [...state.stages, action.stage] };
    }
    case "SET_ERROR":
      return { ...state, error: action.error };
    case "TROUBLESHOOT_STARTED":
      return { ...state, isTroubleshooting: true, liveSteps: [], liveStatus: null };
    case "TROUBLESHOOT_ENDED":
      return { ...state, isTroubleshooting: false, liveSteps: [], liveStatus: null };
    case "ADD_LIVE_STEP":
      return { ...state, liveSteps: [...state.liveSteps, action.step] };
    case "SET_LIVE_STATUS":
      return { ...state, liveStatus: action.text };
    default:
      return state;
  }
}

/* ── Hook ───────────────────────────────────────── */

export function useOpsLoop(incidentId: number) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const abortRef = useRef<AbortController | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const status = await fetchOpsLoopStatus(incidentId);
      dispatch({ type: "SET_STATUS", status });
    } catch {
      // Ignore polling errors
    }
  }, [incidentId]);

  const connect = useCallback(() => {
    // Cancel any in-progress connection
    abortRef.current?.abort();
    if (pollRef.current) clearInterval(pollRef.current);

    const abort = new AbortController();
    abortRef.current = abort;

    // Load initial state
    loadStatus();

    (async () => {
      try {
        const response = await opsLoopStreamSSE(incidentId, abort.signal);
        if (!response.ok || !response.body) {
          // SSE failed — fall back to polling
          startPolling();
          return;
        }

        dispatch({ type: "CONNECTED" });

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
            const data = evt.data as Record<string, unknown>;
            if (evt.event === "loop_stage" && data.stage) {
              const stageName = String(data.stage);

              // Handle intermediate troubleshoot streaming events (ephemeral, not timeline rows)
              if (stageName === "troubleshoot_tool_result") {
                dispatch({
                  type: "ADD_LIVE_STEP",
                  step: {
                    id: uid(),
                    stepName: String(data.step_name ?? ""),
                    content: String(data.content ?? ""),
                    isError: Boolean(data.is_error),
                    toolName: String(data.tool_name ?? ""),
                  },
                });
              } else if (stageName === "troubleshoot_status") {
                dispatch({ type: "SET_LIVE_STATUS", text: String(data.text ?? "") });
              } else if (stageName === "troubleshoot_analysis_done") {
                dispatch({ type: "SET_LIVE_STATUS", text: "Generating analysis..." });
              } else {
                // Regular stage events — add to timeline
                if (stageName === "troubleshoot_started") {
                  dispatch({ type: "TROUBLESHOOT_STARTED" });
                } else if (stageName === "troubleshoot_completed" || stageName === "troubleshoot_failed") {
                  dispatch({ type: "TROUBLESHOOT_ENDED" });
                }

                dispatch({
                  type: "ADD_STAGE",
                  stage: {
                    stage: stageName,
                    timestamp: data.timestamp ? String(data.timestamp) : null,
                    summary: `Ops loop: ${stageName.replace(/_/g, " ")}`,
                    payload: data,
                  },
                });

                if (stageName.includes("completed") || stageName.includes("awaiting")
                    || stageName.includes("failed") || stageName.includes("inconclusive")
                    || stageName.includes("escalation") || stageName.includes("succeeded")
                    || stageName === "retrigger_requested") {
                  loadStatus();
                }
              }
            }
          }
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        dispatch({ type: "SET_ERROR", error: err instanceof Error ? err.message : "Stream error" });
      } finally {
        dispatch({ type: "DISCONNECTED" });
        if (!abort.signal.aborted) startPolling();
      }
    })();

    function startPolling() {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(loadStatus, 10_000);
    }
  }, [incidentId, loadStatus]);

  useEffect(() => {
    connect();
    return () => {
      abortRef.current?.abort();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [connect]);

  return { ...state, refresh: loadStatus };
}
