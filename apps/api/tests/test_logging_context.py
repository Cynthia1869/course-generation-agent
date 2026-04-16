import os
import sys
from pathlib import Path

import pytest

os.environ["APP_ENV"] = "test"
os.environ["DEEPSEEK_API_KEY"] = ""
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.api.deps import get_deepagents_service, get_service
from app.core.schemas import AuditEvent, LLMProviderConfig
from app.llm.deepseek_client import ProviderProfiles


@pytest.fixture(autouse=True)
def isolate_test_state(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_service.cache_clear()
    get_deepagents_service.cache_clear()
    yield
    get_service.cache_clear()
    get_deepagents_service.cache_clear()


@pytest.mark.asyncio
async def test_audit_events_include_model_route_and_prompt_context(monkeypatch: pytest.MonkeyPatch):
    service = get_service()
    events: list[AuditEvent] = []

    service.graph.deepseek.profile = ProviderProfiles(
        chat=LLMProviderConfig(provider="chat-provider", model="chat-model", temperature=0.4),
        clarify=LLMProviderConfig(provider="clarify-provider", model="clarify-model", temperature=0.2),
        extract=LLMProviderConfig(provider="extract-provider", model="extract-model", temperature=0.0),
        generate=LLMProviderConfig(provider="generate-provider", model="generate-model", temperature=0.4),
        review=LLMProviderConfig(provider="review-provider", model="review-model", temperature=0.1),
        improve=LLMProviderConfig(provider="improve-provider", model="improve-model", temperature=0.3),
    )

    async def capture_record(event: AuditEvent) -> None:
        events.append(event)

    monkeypatch.setattr(service.audit, "record", capture_record)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    extract_event = next(event for event in events if event.node_name == "requirement_gap_check")
    generate_event = next(event for event in events if event.node_name == "generate_step_artifact")
    review_event = next(event for event in events if event.node_name == "critique_score")

    assert extract_event.event_type == "GRAPH_NODE_COMPLETED"
    assert extract_event.payload_summary["step_id"] == "course_title"
    assert extract_event.payload_summary["prompt_id"] == "extract.requirements"
    assert extract_event.model_provider == "extract-provider"
    assert extract_event.model_name == "extract-model"
    assert extract_event.profile_name == "extract"

    assert generate_event.event_type == "DRAFT_GENERATED"
    assert generate_event.payload_summary["step_id"] == "course_title"
    assert generate_event.payload_summary["prompt_id"] == "generate.course_title"
    assert generate_event.model_provider == "generate-provider"
    assert generate_event.model_name == "generate-model"
    assert generate_event.profile_name == "generate"
    assert generate_event.artifact_version is not None

    assert review_event.event_type == "REVIEW_BATCH_CREATED"
    assert review_event.payload_summary["step_id"] == "course_title"
    assert review_event.payload_summary["prompt_id"] == "review.course_title"
    assert review_event.payload_summary["review_batch_id"]
    assert review_event.model_provider == "review-provider"
    assert review_event.model_name == "review-model"
    assert review_event.profile_name == "review"


@pytest.mark.asyncio
async def test_clarification_audit_event_keeps_step_and_prompt_context(monkeypatch: pytest.MonkeyPatch):
    service = get_service()
    events: list[AuditEvent] = []

    service.graph.deepseek.profile = ProviderProfiles(
        chat=LLMProviderConfig(provider="chat-provider", model="chat-model", temperature=0.4),
        clarify=LLMProviderConfig(provider="clarify-provider", model="clarify-model", temperature=0.2),
        extract=LLMProviderConfig(provider="extract-provider", model="extract-model", temperature=0.0),
        generate=LLMProviderConfig(provider="generate-provider", model="generate-model", temperature=0.4),
        review=LLMProviderConfig(provider="review-provider", model="review-model", temperature=0.1),
        improve=LLMProviderConfig(provider="improve-provider", model="improve-model", temperature=0.3),
    )

    async def capture_record(event: AuditEvent) -> None:
        events.append(event)

    monkeypatch.setattr(service.audit, "record", capture_record)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一节面向初中生的数学课，先聚焦初二三角函数。",
        "default-user",
    )

    clarify_event = next(event for event in events if event.node_name == "clarify_question")

    assert clarify_event.event_type == "CLARIFICATION_REQUESTED"
    assert clarify_event.payload_summary["step_id"] == "course_title"
    assert clarify_event.payload_summary["prompt_id"] == "clarify.course_title"
    assert clarify_event.model_provider == "clarify-provider"
    assert clarify_event.model_name == "clarify-model"
    assert clarify_event.profile_name == "clarify"
