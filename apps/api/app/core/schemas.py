from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ThreadStatus(str, Enum):
    COLLECTING = "collecting_requirements"
    GENERATING = "generating"
    REVIEW_PENDING = "review_pending"
    REVISING = "revising"
    COMPLETED = "completed"
    FAILED = "failed"


class ReviewSuggestionStatus(str, Enum):
    OPEN = "open"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


class HumanReviewActionType(str, Enum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class RequirementSlot(BaseModel):
    slot_id: str
    label: str
    prompt_hint: str
    value: str | None = None
    confidence: float = 0.0
    source: str = "conversation"
    confirmed: bool = False


class DecisionItem(BaseModel):
    decision_id: str = Field(default_factory=lambda: uuid4().hex)
    topic: str
    value: str
    reason: str
    confirmed_by: str = "user"
    timestamp: datetime = Field(default_factory=utc_now)


class MessageRecord(BaseModel):
    message_id: str = Field(default_factory=lambda: uuid4().hex)
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=utc_now)
    meta: dict[str, Any] = Field(default_factory=dict)


class SourceChunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: uuid4().hex)
    text: str
    index: int


class SourceDocument(BaseModel):
    doc_id: str = Field(default_factory=lambda: uuid4().hex)
    filename: str
    mime_type: str
    extract_status: Literal["pending", "parsed", "failed"] = "pending"
    text_chunks: list[SourceChunk] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DraftArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: uuid4().hex)
    version: int = 1
    markdown: str
    summary: str
    derived_from_feedback_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ReviewCriterionResult(BaseModel):
    criterion_id: str
    name: str
    weight: float
    score: float
    max_score: float
    reason: str


class ReviewSuggestion(BaseModel):
    suggestion_id: str = Field(default_factory=lambda: uuid4().hex)
    criterion_id: str
    problem: str
    suggestion: str
    evidence_span: str
    severity: Literal["low", "medium", "high"] = "medium"
    status: ReviewSuggestionStatus = ReviewSuggestionStatus.OPEN


class ReviewBatch(BaseModel):
    review_batch_id: str = Field(default_factory=lambda: uuid4().hex)
    draft_version: int
    total_score: float
    criteria: list[ReviewCriterionResult]
    suggestions: list[ReviewSuggestion]
    threshold: float = 8.0
    created_at: datetime = Field(default_factory=utc_now)


class HumanReviewAction(BaseModel):
    suggestion_id: str
    action: HumanReviewActionType
    edited_suggestion: str | None = None
    reviewer_id: str = "default-user"
    comment: str | None = None


class InterruptPayload(BaseModel):
    review_batch_id: str
    draft_version: int
    total_score: float
    criteria: list[ReviewCriterionResult]
    suggestions: list[ReviewSuggestion]
    next_expected_action: str = "submit_human_review_actions"


class ResumePayload(BaseModel):
    review_batch_id: str
    review_actions: list[HumanReviewAction]
    submitter_id: str


class VersionRecord(BaseModel):
    version: int
    artifact_id: str
    created_at: datetime = Field(default_factory=utc_now)


class ThreadSummary(BaseModel):
    thread_id: str
    user_id: str
    status: ThreadStatus
    latest_artifact_version: int | None = None
    review_pending: bool = False
    latest_score: float | None = None


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=utc_now)
    level: str = "INFO"
    service: str = "course-agent-backend"
    env: str = "development"
    request_id: str | None = None
    thread_id: str
    run_id: str | None = None
    node_name: str | None = None
    event_type: str
    user_id: str = "default-user"
    artifact_version: int | None = None
    model_provider: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    status: str = "ok"
    error_code: str | None = None
    payload_summary: dict[str, Any] = Field(default_factory=dict)


class ThreadState(BaseModel):
    thread_id: str
    user_id: str = "default-user"
    status: ThreadStatus = ThreadStatus.COLLECTING
    messages: list[MessageRecord] = Field(default_factory=list)
    requirement_slots: dict[str, RequirementSlot] = Field(default_factory=dict)
    decision_ledger: list[DecisionItem] = Field(default_factory=list)
    decision_summary: str = ""
    source_manifest: list[SourceDocument] = Field(default_factory=list)
    draft_artifact: DraftArtifact | None = None
    review_batches: list[ReviewBatch] = Field(default_factory=list)
    approved_feedback: list[HumanReviewAction] = Field(default_factory=list)
    version_chain: list[VersionRecord] = Field(default_factory=list)
    run_metadata: dict[str, Any] = Field(default_factory=dict)


class ApiEnvelope(BaseModel):
    success: bool = True
    request_id: str
    thread_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class SendMessageRequest(BaseModel):
    content: str
    user_id: str = "default-user"


class ReviewSubmitRequest(BaseModel):
    submitter_id: str = "default-user"
    review_actions: list[HumanReviewAction]
