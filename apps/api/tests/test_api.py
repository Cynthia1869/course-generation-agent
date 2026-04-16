import asyncio
import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["APP_ENV"] = "test"
os.environ["DEEPSEEK_API_KEY"] = ""
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.api.deps import get_deepagents_service, get_service
from app.core.schemas import ConfirmStepRequest, DraftArtifact, GenerationSessionState, ModeUpdateRequest, RegenerateRequest, ReviewBatch, ReviewSubmitRequest, ReviewCriterionResult, ThreadState, ThreadSummary
from app.core.settings import get_settings
from app.main import app
from app.series.scoring import SeriesCriterion, SeriesReviewReport, SeriesSuggestion


@pytest.fixture(autouse=True)
def isolate_test_state(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_settings.cache_clear()
    get_service.cache_clear()
    get_deepagents_service.cache_clear()
    yield
    get_settings.cache_clear()
    get_service.cache_clear()
    get_deepagents_service.cache_clear()


async def complete_series_guided_flow(service, thread_id: str):
    messages = [
        "A",
        "我要做一套 AI 产品经理系列课，帮助产品经理把 AI 真正用进需求分析和 PRD 输出流程。",
        "B",
        "D 已经会写 PRD，但不会系统用 AI 提升需求分析效率的产品经理",
        "B",
        "D 从把 AI 当问答工具，转变为把 AI 当产品工作流助手",
        "B",
        "B",
        "要求课程内容贴近日常产品工作，并且后半段一定要有真实案例。",
        "开始生成",
    ]
    for content in messages:
        await service.ingest_message(thread_id, content, "default-user")
    return await service.store.get_thread(thread_id)


@pytest.mark.asyncio
async def test_thread_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/threads")
        assert created.status_code == 200
        thread_id = created.json()["data"]["thread"]["thread_id"]

        listed = await client.get("/api/v1/threads")
        assert listed.status_code == 200
        matching = next(item for item in listed.json()["data"]["threads"] if item["thread_id"] == thread_id)
        assert matching["title"]
        assert matching["subtitle"] in {"继续对话", "继续补充需求"}

        sent = await client.post(
            f"/api/v1/threads/{thread_id}/messages",
            json={"content": "我要做一节面向初中生的数学课，先聚焦初二三角函数。"},
        )
        assert sent.status_code == 200

        thread = await client.get(f"/api/v1/threads/{thread_id}")
        payload = thread.json()["data"]["state"]
        assert payload["draft_artifact"] is None
        assert payload["messages"][-1]["role"] == "assistant"
        assert payload["runtime"]["clarification"]["next_requirement_to_clarify"] in {
            "topic",
            "audience",
            "target_problem",
            "expected_result",
            "tone_style",
        }
        assert payload["runtime"]["clarification"]["next_requirement_to_clarify"] not in {
            "case_preferences",
            "case_variable",
            "case_flow",
            "failure_points",
            "application_scene",
            "script_requirements",
            "resource_requirements",
            "configuration_requirements",
        }


def test_state_defaults_use_new_step_id_system():
    state = ThreadState(thread_id="t-1")
    summary = ThreadSummary(thread_id="t-1", user_id="u-1", status="collecting_requirements")
    assert state.current_step_id == "course_title"
    assert summary.current_step_id == "course_title"
    assert state.current_step_id != "step_1"
    assert summary.current_step_id != "step_1"


@pytest.mark.asyncio
async def test_thread_generation_persists_artifact_and_review_batch():
    service = get_service()
    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")
    state = await service.store.get_thread(thread.thread_id)
    assert state.draft_artifact is not None
    assert state.review_batches
    assert state.review_batches[-1].total_score >= 0


@pytest.mark.asyncio
async def test_start_generate_is_blocked_until_current_step_required_slots_are_complete():
    service = get_service()
    thread = await service.create_thread()

    await service.ingest_message(
        thread.thread_id,
        "我要做一节面向初中生的数学课，先聚焦初二三角函数。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    state = await service.store.get_thread(thread.thread_id)
    assert state.current_step_id == "course_title"
    assert state.draft_artifact is None
    assert state.runtime.clarification.missing_requirements
    assert state.messages[-1].role.value == "assistant"


@pytest.mark.asyncio
async def test_low_score_triggers_auto_optimization_loop(monkeypatch: pytest.MonkeyPatch):
    get_service.cache_clear()
    service = get_service()
    calls = {"review": 0, "improve": 0}

    async def fake_review_markdown(**kwargs):
        markdown = kwargs["markdown"]
        rubric = kwargs["rubric"]
        step_label = kwargs.get("step_label", "当前步骤产物")
        forbidden_topics = kwargs.get("forbidden_topics", "无")
        calls["review"] += 1
        assert step_label == "课程标题"
        assert forbidden_topics
        if calls["review"] == 1:
            return {
                "total_score": 6.5,
                "criteria": [
                    {
                        "criterion_id": item["criterion_id"],
                        "name": item["name"],
                        "weight": item["weight"],
                        "score": 6.0,
                        "max_score": item["max_score"],
                        "reason": "需要优化。",
                    }
                    for item in rubric
                ],
                "suggestions": [
                    {
                        "criterion_id": "script-quality",
                        "problem": "逐字稿过于空泛。",
                        "suggestion": "把案例 2 和案例 3 的讲解扩写成可直接授课的口语化表达。",
                        "evidence_span": "逐字稿",
                        "severity": "high",
                    }
                ],
            }
        return {
            "total_score": 8.6,
            "criteria": [
                {
                    "criterion_id": item["criterion_id"],
                    "name": item["name"],
                    "weight": item["weight"],
                    "score": 8.6,
                    "max_score": item["max_score"],
                    "reason": "已达标。",
                }
                for item in rubric
            ],
            "suggestions": [],
        }

    async def fake_improve_markdown(**kwargs):
        calls["improve"] += 1
        return kwargs["markdown"] + "\n\n## 自动优化说明\n\n已根据评分建议补强逐字稿。"

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)
    monkeypatch.setattr(service.graph.deepseek, "improve_markdown", fake_improve_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")
    state = await service.store.get_thread(thread.thread_id)
    assert calls["review"] >= 2
    assert calls["improve"] == 1
    assert state.runtime.generation_session is not None
    assert state.runtime.generation_session.auto_optimization_loops == 1
    assert state.review_batches[-1].total_score == 8.6


@pytest.mark.asyncio
async def test_timeline_versions_and_regenerate_endpoint(monkeypatch: pytest.MonkeyPatch):
    service = get_service()

    async def fake_improve_markdown(**kwargs):
        assert any("不要咖啡馆案例" in item for item in kwargs["approved_changes"])
        return kwargs["markdown"] + "\n\n## 修订说明\n\n已替换为办公场景案例。"

    async def fake_review_markdown(**kwargs):
        rubric = kwargs["rubric"]
        step_label = kwargs.get("step_label", "当前步骤产物")
        forbidden_topics = kwargs.get("forbidden_topics", "无")
        assert step_label == "课程标题"
        assert forbidden_topics
        return {
            "total_score": 8.6,
            "criteria": [
                {
                    "criterion_id": item["criterion_id"],
                    "name": item["name"],
                    "weight": item["weight"],
                    "score": 8.6,
                    "max_score": item["max_score"],
                    "reason": "已达标。",
                }
                for item in rubric
            ],
            "suggestions": [],
        }

    monkeypatch.setattr(service.graph.deepseek, "improve_markdown", fake_improve_markdown)
    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    state = await service.store.get_thread(thread.thread_id)
    base_version = state.draft_artifact.version

    await service.ingest_message(thread.thread_id, "不要咖啡馆案例", "default-user")
    artifact = await service.regenerate(
        thread.thread_id,
        request=RegenerateRequest(instruction="不要咖啡馆案例，换成办公场景案例", base_version=base_version),
    )

    assert artifact.version >= base_version + 1
    assert artifact.source_version == base_version
    assert "咖啡馆" not in artifact.markdown

    timeline = await service.get_timeline(thread.thread_id)
    event_types = [item.event_type for item in timeline]
    assert "generation_started" in event_types
    assert "revision_completed" in event_types

    versions = await service.list_versions(thread.thread_id)
    assert len(versions) >= 2


@pytest.mark.asyncio
async def test_critique_score_review_receives_current_step_boundary(monkeypatch: pytest.MonkeyPatch):
    service = get_service()
    captured: list[tuple[str, str]] = []

    async def fake_review_markdown(**kwargs):
        captured.append((kwargs.get("step_label", "当前步骤产物"), kwargs.get("forbidden_topics", "无")))
        return {
            "total_score": 8.6,
            "criteria": [],
            "suggestions": [],
        }

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    assert captured[-1] == ("课程标题", "case_details、script_content、material_checklist")


@pytest.mark.asyncio
async def test_regenerate_review_receives_current_step_boundary(monkeypatch: pytest.MonkeyPatch):
    service = get_service()
    captured: list[tuple[str, str]] = []

    async def fake_review_markdown(**kwargs):
        captured.append((kwargs.get("step_label", "当前步骤产物"), kwargs.get("forbidden_topics", "无")))
        return {
            "total_score": 8.6,
            "criteria": [],
            "suggestions": [],
        }

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    state = await service.store.get_thread(thread.thread_id)
    await service.regenerate(thread.thread_id, RegenerateRequest(instruction="换一个标题版本", base_version=state.draft_artifact.version))

    assert captured[-1] == ("课程标题", "case_details、script_content、material_checklist")


@pytest.mark.asyncio
async def test_new_api_endpoints_and_deepagents_experiment():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/threads")
        thread_id = created.json()["data"]["thread"]["thread_id"]

        await client.post(
            f"/api/v1/threads/{thread_id}/messages",
            json={"content": "我要做一节 AI 入门课，主题是提示词，目标是完成日报，时长 60 分钟。"},
        )

        timeline = await client.get(f"/api/v1/threads/{thread_id}/timeline")
        assert timeline.status_code == 200
        assert "timeline" in timeline.json()["data"]

        versions = await client.get(f"/api/v1/threads/{thread_id}/versions")
        assert versions.status_code == 200

        bundle = await client.post(
            "/api/v1/experiments/deepagents/plan",
            json={"thread_id": thread_id, "prompt": "先给我复杂规划建议", "include_thread_context": True},
        )
        assert bundle.status_code == 200
        assert bundle.json()["data"]["bundle"]["summary"]


@pytest.mark.asyncio
async def test_pause_and_delete_thread_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/threads")
        thread_id = created.json()["data"]["thread"]["thread_id"]

        paused = await client.post(f"/api/v1/threads/{thread_id}/pause")
        assert paused.status_code == 200

        thread = await client.get(f"/api/v1/threads/{thread_id}")
        assert thread.json()["data"]["state"]["status"] == "paused"

        resumed = await client.post(f"/api/v1/threads/{thread_id}/resume")
        assert resumed.status_code == 200

        deleted = await client.delete(f"/api/v1/threads/{thread_id}")
        assert deleted.status_code == 200

        missing = await client.get(f"/api/v1/threads/{thread_id}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_pause_cancels_active_generation(monkeypatch: pytest.MonkeyPatch):
    service = get_service()
    service.settings.app_env = "development"
    cancelled = {"value": False}

    async def fake_run_thread(thread_id: str):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled["value"] = True
            raise

    monkeypatch.setattr(service.graph, "run_thread", fake_run_thread)

    thread = await service.create_thread()
    await service.ingest_message(thread.thread_id, "我要做一节数学课", "default-user")
    await asyncio.sleep(0.1)
    await service.pause_thread(thread.thread_id)

    state = await service.store.get_thread(thread.thread_id)
    assert cancelled["value"] is True
    assert state.status.value == "paused"


@pytest.mark.asyncio
async def test_mode_switch_and_step_confirmation_persist_artifact(tmp_path: Path):
    service = get_service()
    thread = await service.create_thread()

    updated = await service.update_mode(thread.thread_id, request=ModeUpdateRequest(mode="series"))
    assert updated.course_mode.value == "series"
    assert updated.current_step_id == "series_framework"
    assert [step.step_id for step in updated.workflow_steps] == ["series_framework"]
    assert "请选择使用方式" in updated.messages[-1].content

    state = await service.store.get_thread(thread.thread_id)
    state.draft_artifact = DraftArtifact(
        version=1,
        markdown="# 系列课程框架\n\n内容",
        summary="系列框架",
    )
    state.review_batches.append(
        ReviewBatch(
            step_id="series_framework",
            draft_version=1,
            total_score=88.0,
            threshold=80.0,
            criteria=[
                ReviewCriterionResult(
                    criterion_id="core-problem",
                    name="核心问题",
                    weight=1.0,
                    score=88.0,
                    max_score=100,
                    reason="达标",
                )
            ],
            suggestions=[],
        )
    )
    await service.store.save_thread(state)

    confirmed = await service.confirm_step(thread.thread_id, request=ConfirmStepRequest(step_id="series_framework"))
    assert confirmed.workflow_steps[0].status.value == "completed"
    assert confirmed.status.value == "completed"
    assert any(item.filename == "series_framework.md" for item in confirmed.saved_artifacts)
    step_artifact = next(item for item in confirmed.step_artifacts if item.step_id == "series_framework")
    assert step_artifact.current_version == 1
    assert step_artifact.confirmed_version == 1


@pytest.mark.asyncio
async def test_completion_gate_all_rejected_but_score_passes(monkeypatch: pytest.MonkeyPatch):
    get_service.cache_clear()
    service = get_service()

    async def fake_score_series_framework_markdown(markdown: str, deepseek):
        return SeriesReviewReport(
            total_score=88.0,
            criteria=[
                SeriesCriterion("目标清晰度", "目标清晰度", 12.0, 88.0, 100.0, "整体达标。"),
                SeriesCriterion("内容逻辑性", "内容逻辑性", 18.0, 86.0, 100.0, "整体达标。"),
            ],
            suggestions=[
                SeriesSuggestion(
                    criterion_id="内容逻辑性",
                    problem="案例还可以更贴近真实工作场景。",
                    suggestion="把后半段示例替换成更贴近日常产品协作的案例。",
                    evidence_span="课程框架",
                    severity="medium",
                )
            ],
            summary="达标。",
        )

    monkeypatch.setattr("app.workflows.course_graph.score_series_framework_markdown", fake_score_series_framework_markdown)

    thread = await service.create_thread()
    await service.update_mode(thread.thread_id, request=ModeUpdateRequest(mode="series"))
    state = await complete_series_guided_flow(service, thread.thread_id)
    batch = state.review_batches[-1]

    await service.submit_review(
        thread.thread_id,
        batch.review_batch_id,
        ReviewSubmitRequest(
            review_actions=[
                {
                    "suggestion_id": batch.suggestions[0].suggestion_id,
                    "action": "reject",
                }
            ]
        ),
    )

    state = await service.store.get_thread(thread.thread_id)
    assert state.status == "review_pending"

    confirmed = await service.confirm_step(thread.thread_id, ConfirmStepRequest(step_id="series_framework"))
    assert confirmed.status.value == "completed"


@pytest.mark.asyncio
async def test_interrupt_resume_survives_service_restart(monkeypatch: pytest.MonkeyPatch):
    get_service.cache_clear()
    service = get_service()

    async def fake_score_series_framework_markdown(markdown: str, deepseek):
        return SeriesReviewReport(
            total_score=85.0,
            criteria=[
                SeriesCriterion("目标清晰度", "目标清晰度", 12.0, 84.0, 100.0, "整体达标。"),
                SeriesCriterion("实战性", "实战性", 14.0, 83.0, 100.0, "整体达标。"),
            ],
            suggestions=[
                SeriesSuggestion(
                    criterion_id="实战性",
                    problem="结尾还可以再加强收束感。",
                    suggestion="让最后一课增加更完整的复盘和迁移说明。",
                    evidence_span="课程框架",
                    severity="low",
                )
            ],
            summary="达标。",
        )

    monkeypatch.setattr("app.workflows.course_graph.score_series_framework_markdown", fake_score_series_framework_markdown)

    thread = await service.create_thread()
    await service.update_mode(thread.thread_id, request=ModeUpdateRequest(mode="series"))
    await complete_series_guided_flow(service, thread.thread_id)

    original_state = await service.store.get_thread(thread.thread_id)
    batch = original_state.review_batches[-1]

    get_service.cache_clear()
    restarted = get_service()
    monkeypatch.setattr("app.workflows.course_graph.score_series_framework_markdown", fake_score_series_framework_markdown)

    await restarted.submit_review(
        thread.thread_id,
        batch.review_batch_id,
        ReviewSubmitRequest(
            review_actions=[
                {
                    "suggestion_id": batch.suggestions[0].suggestion_id,
                    "action": "reject",
                }
            ]
        ),
    )

    resumed_state = await restarted.store.get_thread(thread.thread_id)
    assert resumed_state.status == "review_pending"

    confirmed = await restarted.confirm_step(thread.thread_id, ConfirmStepRequest(step_id="series_framework"))
    assert confirmed.status.value == "completed"


@pytest.mark.asyncio
async def test_auto_optimization_loops_do_not_leak_into_regenerate(monkeypatch: pytest.MonkeyPatch):
    get_service.cache_clear()
    service = get_service()
    calls = {"review": 0}

    async def fake_review_markdown(**kwargs):
        rubric = kwargs["rubric"]
        step_label = kwargs.get("step_label", "当前步骤产物")
        forbidden_topics = kwargs.get("forbidden_topics", "无")
        assert step_label == "课程标题"
        assert forbidden_topics
        calls["review"] += 1
        if calls["review"] == 1:
            return {
                "total_score": 6.5,
                "criteria": [
                    {
                        "criterion_id": item["criterion_id"],
                        "name": item["name"],
                        "weight": item["weight"],
                        "score": 6.0,
                        "max_score": item["max_score"],
                        "reason": "需要优化。",
                    }
                    for item in rubric
                ],
                "suggestions": [
                    {
                        "criterion_id": "script-quality",
                        "problem": "逐字稿过于空泛。",
                        "suggestion": "补强案例讲解。",
                        "evidence_span": "逐字稿",
                        "severity": "high",
                    }
                ],
            }
        return {
            "total_score": 8.9,
            "criteria": [
                {
                    "criterion_id": item["criterion_id"],
                    "name": item["name"],
                    "weight": item["weight"],
                    "score": 8.9,
                    "max_score": item["max_score"],
                    "reason": "已达标。",
                }
                for item in rubric
            ],
            "suggestions": [],
        }

    async def fake_improve_markdown(**kwargs):
        return kwargs["markdown"] + "\n\n## 补强\n\n已补强案例。"

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)
    monkeypatch.setattr(service.graph.deepseek, "improve_markdown", fake_improve_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长 60 分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    state = await service.store.get_thread(thread.thread_id)
    base_version = state.draft_artifact.version
    assert state.runtime.generation_session is not None
    assert state.runtime.generation_session.auto_optimization_loops == 1

    await service.regenerate(
        thread.thread_id,
        RegenerateRequest(instruction="换成新的课堂案例", base_version=base_version),
    )

    state = await service.store.get_thread(thread.thread_id)
    assert state.runtime.generation_session is None or state.runtime.generation_session.auto_optimization_loops == 0


@pytest.mark.asyncio
async def test_mode_specific_steps_are_distinct():
    service = get_service()
    thread = await service.create_thread()
    state = await service.store.get_thread(thread.thread_id)
    assert [step.step_id for step in state.workflow_steps] == [
        "course_title",
        "course_framework",
        "case_output",
        "script_output",
        "material_checklist",
    ]

    updated = await service.update_mode(thread.thread_id, ModeUpdateRequest(mode="series"))
    assert [step.step_id for step in updated.workflow_steps] == ["series_framework"]


@pytest.mark.asyncio
async def test_series_mode_runs_guided_questionnaire_before_generation():
    service = get_service()
    thread = await service.create_thread()
    await service.update_mode(thread.thread_id, ModeUpdateRequest(mode="series"))

    state = await service.store.get_thread(thread.thread_id)
    assert state.messages[-1].content.startswith("请选择使用方式")

    await service.ingest_message(thread.thread_id, "A", "default-user")
    state = await service.store.get_thread(thread.thread_id)
    assert state.messages[-1].content.startswith("请输入你的制课想法")

    await service.ingest_message(thread.thread_id, "我要做一套 AI 产品经理系列课。", "default-user")
    state = await service.store.get_thread(thread.thread_id)
    assert state.messages[-1].content.startswith("系列课结构化问答 1/7")

    await service.ingest_message(thread.thread_id, "B", "default-user")
    state = await service.store.get_thread(thread.thread_id)
    assert state.runtime.series_guided.current_question_id == "target_user"


@pytest.mark.asyncio
async def test_series_guided_questionnaire_does_not_wait_for_remote_requirement_extraction(monkeypatch: pytest.MonkeyPatch):
    service = get_service()
    thread = await service.create_thread()
    await service.update_mode(thread.thread_id, ModeUpdateRequest(mode="series"))

    async def fail_if_called(**kwargs):
        raise AssertionError("series guided questionnaire should not call remote requirement extraction")

    monkeypatch.setattr(service.graph.deepseek, "extract_requirements", fail_if_called)

    await service.ingest_message(thread.thread_id, "A", "default-user")
    await service.ingest_message(thread.thread_id, "我要做一门 AI 编程入门系列课。", "default-user")

    state = await service.store.get_thread(thread.thread_id)
    assert state.runtime.series_guided.current_question_id == "course_type"
    assert state.messages[-1].content.startswith("系列课结构化问答 1/7")


@pytest.mark.asyncio
async def test_series_framework_file_upload_runs_review_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/threads")
        thread_id = created.json()["data"]["thread"]["thread_id"]

        updated = await client.patch(
            f"/api/v1/threads/{thread_id}/mode",
            json={"mode": "series", "user_id": "default-user"},
        )
        assert updated.status_code == 200

        framework_markdown = """课程名称：AI 产品经理需求分析系列课
目标学员：已经会写 PRD，但不会系统用 AI 提升需求分析效率的产品经理
学员当前状态：会做基础需求分析，但没有把 AI 接进自己的产品工作流
学员期望状态：可以把 AI 用进需求洞察、PRD 结构化和复盘优化流程
思维转换：从把 AI 当问答工具，到把 AI 当产品工作流助手
课程核心问题：如何让产品经理把 AI 真正用进需求分析与 PRD 输出流程
课程应用场景：日常需求分析、方案拆解、PRD 产出和跨团队协作场景

第1课：认识 AI 产品工作流
内容：明确 AI 在需求分析流程中的角色定位和边界。

第2课：用 AI 做需求洞察
内容：围绕用户反馈、访谈纪要和数据线索完成问题整理。

第3课：用 AI 提升 PRD 输出效率
内容：把需求分析结果转成结构更完整、表达更清晰的 PRD 初稿。

第4课：案例实战与复盘
内容：围绕真实产品需求案例演练完整工作流并复盘优化。
"""

        uploaded = await client.post(
            f"/api/v1/threads/{thread_id}/files?category=framework",
            files={"file": ("series_framework.md", framework_markdown.encode("utf-8"), "text/markdown")},
        )
        assert uploaded.status_code == 200

        thread = await client.get(f"/api/v1/threads/{thread_id}")
        state = thread.json()["data"]["state"]
        assert state["runtime"]["series_guided"]["using_existing_framework"] is True
        assert state["runtime"]["series_guided"]["awaiting_framework_input"] is False
        assert state["draft_artifact"] is not None
        assert state["review_batches"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_current_step", "expected_steps"),
    [
        (
            "single",
            "course_title",
            ["course_title", "course_framework", "case_output", "script_output", "material_checklist"],
        ),
        ("series", "series_framework", ["series_framework"]),
    ],
)
async def test_mode_to_step_mapping_matches_product_boundary(mode: str, expected_current_step: str, expected_steps: list[str]):
    service = get_service()
    thread = await service.create_thread()

    if mode == "series":
        state = await service.update_mode(thread.thread_id, ModeUpdateRequest(mode="series"))
    else:
        state = await service.store.get_thread(thread.thread_id)

    assert state.current_step_id == expected_current_step
    assert [step.step_id for step in state.workflow_steps] == expected_steps
    assert [step.status.value for step in state.workflow_steps].count("active") == 1
    assert state.workflow_steps[0].status.value == "active"


@pytest.mark.asyncio
async def test_switching_mode_clears_stale_artifact_review_and_generation_context():
    service = get_service()
    thread = await service.create_thread()

    state = await service.store.get_thread(thread.thread_id)
    state.draft_artifact = DraftArtifact(version=3, markdown="# 旧稿", summary="旧稿")
    state.review_batches.append(
        ReviewBatch(
            step_id="course_title",
            draft_version=3,
            total_score=8.4,
            threshold=8.0,
            criteria=[
                ReviewCriterionResult(
                    criterion_id="core-problem",
                    name="核心问题",
                    weight=1.0,
                    score=8.4,
                    max_score=10,
                    reason="达标",
                )
            ],
            suggestions=[],
        )
    )
    state.runtime.generation_session = GenerationSessionState(step_id="course_title")
    state.runtime.pending_manual_revision_request = "把案例换掉"
    await service.store.save_thread(state)

    updated = await service.update_mode(thread.thread_id, ModeUpdateRequest(mode="series"))

    assert updated.course_mode.value == "series"
    assert updated.current_step_id == "series_framework"
    assert [step.step_id for step in updated.workflow_steps] == ["series_framework"]
    assert updated.draft_artifact is None
    assert updated.review_batches == []
    assert updated.runtime.generation_session is None
    assert updated.runtime.pending_manual_revision_request is None
    assert updated.runtime.series_guided.awaiting_entry_mode is True
    assert "请选择使用方式" in updated.messages[-1].content


@pytest.mark.asyncio
async def test_clarification_gate_blocks_generation_even_if_user_says_start_generate():
    service = get_service()
    thread = await service.create_thread()

    await service.ingest_message(
        thread.thread_id,
        "我要做一节面向初中生的数学课，开始生成。",
        "default-user",
    )

    state = await service.store.get_thread(thread.thread_id)
    assert state.draft_artifact is None
    assert not state.review_batches
    assert state.status.value == "collecting_requirements"
    assert state.runtime.clarification.next_requirement_to_clarify in {"topic", "target_problem", "expected_result", "tone_style"}
    assert state.messages[-1].role.value == "assistant"
    assert state.messages[-1].content.startswith("当前在“课程标题”这一步，为了继续往下走，我只需要你补充一个信息：")


@pytest.mark.asyncio
async def test_confirm_gate_requires_explicit_start_generate_after_required_slots_are_complete():
    service = get_service()
    thread = await service.create_thread()

    await service.ingest_message(
        thread.thread_id,
        "我要做一门课，主题是三角函数，给初中生，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练。",
        "default-user",
    )

    state = await service.store.get_thread(thread.thread_id)
    assert state.runtime.clarification.missing_requirements == []
    assert state.draft_artifact is None
    assert not state.review_batches
    assert state.status.value == "collecting_requirements"
    assert state.messages[-1].role.value == "assistant"
    assert "如果这些信息没问题，你回复“开始生成”即可。" in state.messages[-1].content
    assert "这一步只会生成“课程标题”相关内容" in state.messages[-1].content


@pytest.mark.asyncio
async def test_series_review_gate_keeps_series_framework_pending_after_generation(monkeypatch: pytest.MonkeyPatch):
    service = get_service()

    async def fake_score_series_framework_markdown(markdown: str, deepseek):
        return SeriesReviewReport(
            total_score=88.0,
            criteria=[SeriesCriterion("目标清晰度", "目标清晰度", 12.0, 88.0, 100.0, "整体达标。")],
            suggestions=[
                SeriesSuggestion(
                    criterion_id="内容逻辑性",
                    problem="案例还可以更贴近课堂。",
                    suggestion="把案例改成更贴近课堂的练习场景。",
                    evidence_span="课程框架",
                    severity="medium",
                )
            ],
            summary="达标。",
        )

    monkeypatch.setattr("app.workflows.course_graph.score_series_framework_markdown", fake_score_series_framework_markdown)

    thread = await service.create_thread()
    await service.update_mode(thread.thread_id, ModeUpdateRequest(mode="series"))
    state = await complete_series_guided_flow(service, thread.thread_id)
    batch = state.review_batches[-1]

    assert state.status.value == "review_pending"
    assert state.workflow_steps[0].status.value == "active"
    assert state.draft_artifact is not None
    assert state.runtime.human_review.interrupt_payload is not None
    assert state.runtime.human_review.interrupt_payload.review_batch_id == batch.review_batch_id
    assert state.runtime.human_review.interrupt_payload.total_score == batch.total_score


@pytest.mark.asyncio
async def test_confirm_step_api_reports_review_gate_when_latest_review_score_is_below_threshold():
    service = get_service()
    thread = await service.create_thread()
    await service.update_mode(thread.thread_id, ModeUpdateRequest(mode="series"))

    state = await service.store.get_thread(thread.thread_id)
    state.draft_artifact = DraftArtifact(version=1, markdown="# 系列课程框架\n\n内容", summary="系列框架")
    state.review_batches.append(
        ReviewBatch(
            step_id="series_framework",
            draft_version=1,
            total_score=71.0,
            threshold=80.0,
            criteria=[
                ReviewCriterionResult(
                    criterion_id="core-problem",
                    name="核心问题",
                    weight=1.0,
                    score=71.0,
                    max_score=100,
                    reason="未达标",
                )
            ],
            suggestions=[],
        )
    )
    await service.store.save_thread(state)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/threads/{thread.thread_id}/confirm-step",
            json={"step_id": "series_framework"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "step_confirmation_rejected"
    assert response.json()["detail"]["message"] == "Current step review score does not meet the threshold"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "expected_kind", "expected_step_id"),
    [
        ("context", "reference", "course_title"),
        ("package", "uploaded", "package_upload"),
    ],
)
async def test_upload_only_updates_files_and_never_enters_generation_state_machine(category: str, expected_kind: str, expected_step_id: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/threads")
        thread_id = created.json()["data"]["thread"]["thread_id"]

        uploaded = await client.post(
            f"/api/v1/threads/{thread_id}/files?category={category}",
            files={"file": ("notes.txt", "三角函数资料", "text/plain")},
        )
        assert uploaded.status_code == 200

        thread = await client.get(f"/api/v1/threads/{thread_id}")
        state = thread.json()["data"]["state"]
        timeline = await client.get(f"/api/v1/threads/{thread_id}/timeline")
        saved = next(item for item in state["saved_artifacts"] if item["filename"] == "notes.txt")

        assert state["status"] == "collecting_requirements"
        assert state["draft_artifact"] is None
        assert state["runtime"]["generation_session"] is None
        assert state["generation_runs"] == []
        assert len(state["source_manifest"]) == 1
        assert saved["kind"] == expected_kind
        assert saved["step_id"] == expected_step_id
        assert "generation_started" not in [item["event_type"] for item in timeline.json()["data"]["timeline"]]
        assert "review_ready" not in [item["event_type"] for item in timeline.json()["data"]["timeline"]]


@pytest.mark.asyncio
async def test_confirm_step_rejects_when_artifact_or_review_missing():
    service = get_service()
    thread = await service.create_thread()

    with pytest.raises(ValueError):
        await service.confirm_step(thread.thread_id, ConfirmStepRequest(step_id="course_title"))


@pytest.mark.asyncio
async def test_step_artifact_lifecycle_tracks_current_version():
    service = get_service()
    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长 60 分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    state = await service.store.get_thread(thread.thread_id)
    step_artifact = next(item for item in state.step_artifacts if item.step_id == "course_title")
    assert step_artifact.current_version == state.draft_artifact.version
    assert step_artifact.current_artifact_id == state.draft_artifact.artifact_id
    assert step_artifact.status.value == "generated"
    assert step_artifact.confirmed_version is None

    state = await service.store.get_thread(thread.thread_id)
    state.draft_artifact = DraftArtifact(version=1, markdown="# 标题", summary="标题")
    await service.store.save_thread(state)

    with pytest.raises(ValueError):
        await service.confirm_step(thread.thread_id, ConfirmStepRequest(step_id="course_title"))
