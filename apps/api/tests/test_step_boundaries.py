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
from app.core.schemas import ConfirmStepRequest, DraftArtifact, ReviewBatch, StepArtifactRecord, StepArtifactStatus
from app.core.step_catalog import MODE_STEP_IDS
from app.core.schemas import CourseMode, RegenerateRequest


@pytest.fixture(autouse=True)
def isolate_test_state(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_service.cache_clear()
    get_deepagents_service.cache_clear()
    yield
    get_service.cache_clear()
    get_deepagents_service.cache_clear()


@pytest.mark.asyncio
async def test_single_course_step_order_is_fixed():
    assert MODE_STEP_IDS[CourseMode.SINGLE] == (
        "course_title",
        "course_framework",
        "case_output",
        "script_output",
        "material_checklist",
    )


@pytest.mark.asyncio
async def test_future_step_cannot_be_confirmed_before_active_step():
    service = get_service()
    thread = await service.create_thread()

    await service.ingest_message(
        thread.thread_id,
        "我要做一门课，主题是三角函数，给初中生，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练。",
        "default-user",
    )

    with pytest.raises(ValueError, match="Only the active step can be confirmed"):
        await service.confirm_step(thread.thread_id, ConfirmStepRequest(step_id="course_framework"))


@pytest.mark.asyncio
async def test_prompt_context_only_uses_confirmed_prior_step_artifacts():
    service = get_service()
    thread = await service.create_thread()
    state = await service.store.get_thread(thread.thread_id)

    state.current_step_id = "script_output"
    state.step_artifacts = [
        StepArtifactRecord(
            step_id="course_title",
            label="课程标题",
            status=StepArtifactStatus.CONFIRMED,
            confirmed_artifact_id="a1",
            confirmed_version=1,
            current_artifact_id="a1",
            current_version=1,
        ),
        StepArtifactRecord(
            step_id="course_framework",
            label="课程框架",
            status=StepArtifactStatus.CONFIRMED,
            confirmed_artifact_id="a2",
            confirmed_version=2,
            current_artifact_id="a2",
            current_version=2,
        ),
        StepArtifactRecord(
            step_id="case_output",
            label="案例输出",
            status=StepArtifactStatus.GENERATED,
            current_artifact_id="a3",
            current_version=3,
        ),
    ]
    await service.store.upsert_artifact_version(
        thread.thread_id,
        DraftArtifact(step_id="course_title", artifact_id="a1", version=1, markdown="# 标题", summary="标题"),
    )
    await service.store.upsert_artifact_version(
        thread.thread_id,
        DraftArtifact(step_id="course_framework", artifact_id="a2", version=2, markdown="# 框架", summary="框架"),
    )
    await service.store.upsert_artifact_version(
        thread.thread_id,
        DraftArtifact(step_id="case_output", artifact_id="a3", version=3, markdown="# 案例", summary="案例"),
    )
    await service.store.save_thread(state)

    refreshed = await service.store.get_thread(thread.thread_id)
    layers = await service.graph._build_prompt_context_layers(refreshed, service.graph._current_step(refreshed))
    confirmed_text = service.graph._confirmed_artifacts_text(layers.confirmed_artifacts)

    assert "# 标题" in confirmed_text
    assert "# 框架" in confirmed_text
    assert "# 案例" not in confirmed_text


@pytest.mark.asyncio
async def test_future_step_user_message_does_not_pollute_current_step_generation_context(monkeypatch: pytest.MonkeyPatch):
    service = get_service()
    captured: dict[str, str] = {}

    async def fake_stream_step_markdown(context: dict):
        captured.update(context)
        yield "# 标题候选"

    async def fake_review_markdown(**kwargs):
        return {"total_score": 8.6, "criteria": [], "suggestions": []}

    monkeypatch.setattr(service.graph.deepseek, "stream_step_markdown", fake_stream_step_markdown)
    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练。另外案例一定要用咖啡馆经营场景。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    assert captured["prompt_id"] == "generate.course_title"
    assert "主题: 三角函数" in captured["structured_inputs"]
    assert "咖啡馆经营场景" not in captured["structured_inputs"]
    assert captured["confirmed_artifacts"] == "暂无已确认前序产物"


@pytest.mark.asyncio
async def test_manual_feedback_only_updates_current_step_not_requirement_slots(monkeypatch: pytest.MonkeyPatch):
    service = get_service()

    async def fake_review_markdown(**kwargs):
        return {"total_score": 8.6, "criteria": [], "suggestions": []}

    async def fake_improve_markdown(**kwargs):
        return kwargs["markdown"] + "\n\n## 修订\n\n当前步骤已修订。"

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)
    monkeypatch.setattr(service.graph.deepseek, "improve_markdown", fake_improve_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    before = await service.store.get_thread(thread.thread_id)
    original_topic = before.requirement_slots["topic"].value
    base_version = before.draft_artifact.version

    await service.ingest_message(thread.thread_id, "继续修改：标题更像提分课，但不要改主题。", "default-user")

    after = await service.store.get_thread(thread.thread_id)
    assert after.requirement_slots["topic"].value == original_topic
    assert after.draft_artifact.version == base_version + 1
    assert after.draft_artifact.step_id == "course_title"


@pytest.mark.asyncio
async def test_regenerate_rejects_previous_step_version_after_step_switch(monkeypatch: pytest.MonkeyPatch):
    service = get_service()

    async def fake_review_markdown(**kwargs):
        return {"total_score": 8.7, "criteria": [], "suggestions": []}

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")
    await service.confirm_step(thread.thread_id, ConfirmStepRequest(step_id="course_title"))

    state = await service.store.get_thread(thread.thread_id)
    assert state.current_step_id == "course_framework"

    with pytest.raises(ValueError, match="Only the active step artifact can be regenerated"):
        await service.regenerate(
            thread.thread_id,
            RegenerateRequest(instruction="重写上一版标题", base_version=1),
        )
