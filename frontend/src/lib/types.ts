export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  steps: StepData[];
  timestamp: number;
}

export interface StepData {
  id: string;
  name: string;
  content: string;
  toolName: string;
  isError: boolean;
}

// SSE event data types
export interface RoutingEvent {
  agent: string;
  batch_count: number | null;
}

export interface StatusEvent {
  text: string;
  tool_name: string | null;
  args: Record<string, unknown> | null;
}

export interface ProgressState {
  current: number;
  total: number;
}

export interface ToolResultEvent {
  tool_name: string;
  step_name: string;
  content: string;
  is_error: boolean;
  raw: string;
}

export interface AnalystTokenEvent {
  token: string;
}

export interface AnalystDoneEvent {
  full_content: string;
}

export interface ErrorEvent {
  message: string;
  type: string;
}

export interface SSEEvent {
  event: string;
  data: RoutingEvent | StatusEvent | ToolResultEvent | AnalystTokenEvent | AnalystDoneEvent | ErrorEvent | Record<string, never>;
}

// Chat state for useReducer
export interface ChatState {
  sessionId: string | null;
  messages: ChatMessage[];
  isStreaming: boolean;
  currentStatus: string | null;
  statusHistory: string[];
  currentProgress: ProgressState | null;
  currentSteps: StepData[];
  streamingTokens: string;
  /** When set, the streaming text animation should finish, then call COMMIT_FINALIZE. */
  pendingFinalContent: string | null;
  error: string | null;
}

export type ChatAction =
  | { type: "SET_SESSION"; sessionId: string }
  | { type: "ADD_USER_MESSAGE"; message: ChatMessage }
  | { type: "SET_STREAMING"; isStreaming: boolean }
  | { type: "SET_STATUS"; text: string | null; progress?: ProgressState | null }
  | { type: "ADD_STEP"; step: StepData }
  | { type: "APPEND_TOKEN"; token: string }
  | { type: "FINALIZE_ASSISTANT"; content: string }
  | { type: "COMMIT_FINALIZE" }
  | { type: "FINALIZE_ORPHAN" }
  | { type: "SET_ERROR"; error: string | null }
  | { type: "RESET_CHAT" };
