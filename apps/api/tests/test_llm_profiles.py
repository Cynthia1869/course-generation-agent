import os
import sys
from pathlib import Path

import pytest  # type: ignore

os.environ["APP_ENV"] = "test"
os.environ["DEEPSEEK_API_KEY"] = ""
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.api.deps import get_service
from app.core.settings import get_settings
from app.llm.deepseek_client import DeepSeekClient


@pytest.fixture(autouse=True)
def isolate_test_state(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_settings.cache_clear()
    get_service.cache_clear()
    yield
    get_settings.cache_clear()
    get_service.cache_clear()


def test_deepseek_profiles_split_actions_by_config():
    client = DeepSeekClient(get_settings())
    assert client.get_profile("clarify").model == "deepseek-chat"
    assert client.get_profile("extract").model == "deepseek-chat"
    assert client.get_profile("generate").model == "deepseek-reasoner"
    assert client.get_profile("review").model == "deepseek-reasoner"
    assert client.get_profile("improve").model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_generation_run_records_actual_generate_profile(monkeypatch: pytest.MonkeyPatch):
    service = get_service()

    async def fake_review_markdown(**kwargs):
        return {"total_score": 8.6, "criteria": [], "suggestions": []}

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    state = await service.store.get_thread(thread.thread_id)
    run = state.generation_runs[0]
    assert run.profile_name == "generate"
    assert run.model_name == "deepseek-reasoner"
    assert run.prompt_id == "generate.course_title"


@pytest.mark.asyncio
async def test_audit_records_prompt_and_profile_for_llm_nodes(monkeypatch: pytest.MonkeyPatch):
    service = get_service()

    async def fake_review_markdown(**kwargs):
        return {"total_score": 8.6, "criteria": [], "suggestions": []}

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一门入门课，给初中生，主题是三角函数，解决基础题不会做的问题，学完能独立完成基础题，风格实操带练，时长90分钟，要求基于真实案例。",
        "default-user",
    )
    await service.ingest_message(thread.thread_id, "开始生成", "default-user")

    events = await service.audit.list_events(thread.thread_id)
    generated = next(event for event in events if event.event_type == "DRAFT_GENERATED")
    reviewed = next(event for event in events if event.event_type == "REVIEW_BATCH_CREATED")

    assert generated.prompt_id == "generate.course_title"
    assert generated.profile_name == "generate"
    assert generated.model_name == "deepseek-reasoner"

    assert reviewed.prompt_id == "review.course_title"
    assert reviewed.profile_name == "review"
    assert reviewed.model_name == "deepseek-reasoner"
