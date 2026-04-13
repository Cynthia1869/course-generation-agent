export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface MessageRecord {
  message_id: string;
  role: MessageRole;
  content: string;
  timestamp: string;
}

export interface DraftArtifact {
  artifact_id: string;
  version: number;
  markdown: string;
  summary: string;
}

export interface ReviewCriterionResult {
  criterion_id: string;
  name: string;
  weight: number;
  score: number;
  max_score: number;
  reason: string;
}

export interface ReviewSuggestion {
  suggestion_id: string;
  criterion_id: string;
  problem: string;
  suggestion: string;
  evidence_span: string;
  severity: "low" | "medium" | "high";
  status: "open" | "approved" | "edited" | "rejected";
}

export interface ReviewBatch {
  review_batch_id: string;
  draft_version: number;
  total_score: number;
  criteria: ReviewCriterionResult[];
  suggestions: ReviewSuggestion[];
}

export interface ThreadState {
  thread_id: string;
  status: string;
  messages: MessageRecord[];
  draft_artifact: DraftArtifact | null;
  review_batches: ReviewBatch[];
}
