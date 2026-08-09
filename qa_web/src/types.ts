export type AnswerStatus =
  | "FULL"
  | "NEEDS_CLARIFICATION"
  | "SAFE_LIMITED"
  | "UNAVAILABLE"
  | "RETRYABLE_ERROR";

export type FeedbackVerdict = "accurate" | "partly_accurate" | "incorrect" | "uncertain";

export type FeedbackTag =
  | "WRONG_PRODUCT"
  | "WRONG_VALUE"
  | "MISSING_CONDITION"
  | "BAD_CLARIFICATION"
  | "WRONG_COMPARISON"
  | "EVIDENCE_MISMATCH"
  | "UNSAFE_LANGUAGE"
  | "SLOW"
  | "OTHER";

export interface ClarificationOption {
  label: string;
  value: string;
  description?: string;
}

export interface ClarificationView {
  id: string;
  question: string;
  options: ClarificationOption[];
  expires_at?: string | null;
  consumed?: boolean;
}

export interface ConditionEntry {
  condition_id?: string;
  kind?: string;
  requested_text?: string;
  grounded_fields?: string[];
  note?: string;
  condition?: string;
  field?: string;
  operator?: string;
  requested_value?: unknown;
  grounded_value?: unknown;
  status?: string;
  explanation?: string;
}

export interface EvidenceSource {
  evidence_id?: string;
  metric_id?: string;
  source_table_id?: string;
  source_file?: string;
  source_sheet?: string;
  source_excel_row?: number | string;
  source_field?: string;
  raw_value?: unknown;
  normalized_value?: unknown;
  source_row_hash?: string;
  quality_flags?: string[];
  field?: string;
  value?: unknown;
  unit?: string | null;
  as_of_date?: string | null;
  quality_status?: string;
  row_hash?: string;
}

export interface EvidenceItem {
  product_uid?: string;
  name?: string;
  rank?: number | null;
  product_name?: string;
  scope?: string;
  fields?: EvidenceSource[];
  sources?: EvidenceSource[];
}

export interface RetrievalChannel {
  channel?: string;
  reason?: string;
  candidate_count?: number;
  verified_count?: number | null;
  fallback?: string | null;
  fallback_reason?: string | null;
  scope?: string;
  latency_ms?: number;
  evidence_refs?: string[];
  status?: string;
}

export interface ConditionChange {
  kind: string;
  previous: string[];
  current: string;
  reason?: string;
}

export interface EvidenceView {
  snapshot_date?: string | null;
  scope?: string | null;
  result_count?: number | null;
  items?: EvidenceItem[];
  aggregates?: Array<{
    aggregate_id?: string;
    group_key?: string;
    value?: unknown;
    unit?: string | null;
    source_table_ids?: string[];
    source_fields?: string[];
    source_row_count?: number;
    query_hash?: string;
    as_of_date?: string | null;
  }>;
  limitations?: string[];
  condition_ledger?: ConditionEntry[];
  condition_changes?: ConditionChange[];
  retrieval_channels?: RetrievalChannel[];
}

export interface EnvironmentView {
  engine_git_sha?: string;
  image_digest?: string;
  data_hash?: string;
  model_id?: string;
  planner_stage?: string;
  vector_status?: string;
  data_snapshot_date?: string;
}

export interface AssistantMessage {
  id: string;
  status: AnswerStatus;
  content: string;
  answerability?: string;
  reason_code?: string | null;
  clarification?: ClarificationView | null;
  evidence?: EvidenceView;
  environment?: EnvironmentView;
  created_at?: string;
  feedback?: FeedbackRecord | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at?: string;
  assistant?: AssistantMessage;
}

export interface FeedbackRecord {
  message_id?: string;
  verdict: FeedbackVerdict;
  tags: FeedbackTag[];
  note?: string;
  updated_at?: string;
}

export interface QaSession {
  id: string;
  session_version: number;
  version?: number;
  title: string;
  mode: "guided" | "free";
  scenario_id?: string | null;
  created_at?: string;
  updated_at?: string;
  messages: ChatMessage[];
}

export interface SessionSummary {
  id: string;
  session_version: number;
  version?: number;
  title: string;
  mode: "guided" | "free";
  scenario_id?: string | null;
  updated_at?: string;
  message_count?: number;
}

export interface TurnResponse {
  session_id: string;
  session_version: number;
  turn_id: number;
  assistant: AssistantMessage;
}

export interface TesterProfile {
  id: string;
  alias: string;
  csrf_token?: string;
  consented_at?: string;
}

export interface QaStatus {
  status: "READY" | "DEGRADED" | "DISABLED";
  ready: boolean;
  pilot_chat_enabled: boolean;
  environment?: EnvironmentView;
  data_snapshot_date?: string;
  engine_git_sha?: string;
  data_hash?: string;
  model_id?: string;
  planner_stage?: string;
  vector_status?: string;
  reason?: string;
  retention_days: number;
  release_gate?: string;
  engine?: { status?: string };
}

export interface GuidedScenario {
  id: string;
  title: string;
  category: string;
  description: string;
  starter: string;
}
