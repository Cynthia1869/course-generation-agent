import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["APP_ENV"] = "test"
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.api.deps import get_service
from app.main import app


@pytest.mark.asyncio
async def test_thread_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/threads")
        assert created.status_code == 200
        thread_id = created.json()["data"]["thread"]["thread_id"]

        sent = await client.post(
            f"/api/v1/threads/{thread_id}/messages",
            json={"content": "我要做一节企业内训课，主题是提示词设计，学员是运营团队。"},
        )
        assert sent.status_code == 200

        thread = await client.get(f"/api/v1/threads/{thread_id}")
        payload = thread.json()["data"]["state"]
        assert payload["draft_artifact"] is None
        assert payload["messages"][-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_thread_generation_persists_artifact_and_review_batch():
    service = get_service()
    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一节企业内训课，主题是提示词设计，学员是运营团队，目标是能写出稳定提示词，时长90分钟，限制是要基于真实案例。",
        "default-user",
    )
    state = await service.store.get_thread(thread.thread_id)
    assert state.draft_artifact is not None
    assert state.review_batches
    assert state.review_batches[-1].total_score >= 0


@pytest.mark.asyncio
async def test_low_score_triggers_auto_optimization_loop(monkeypatch: pytest.MonkeyPatch):
    get_service.cache_clear()
    service = get_service()
    calls = {"review": 0, "improve": 0}

    async def fake_review_markdown(*, markdown: str, rubric: list[dict], threshold: float):
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

    async def fake_improve_markdown(*, markdown: str, approved_changes: list[str], context_summary: str):
        calls["improve"] += 1
        return markdown + "\n\n## 自动优化说明\n\n已根据评分建议补强逐字稿。"

    monkeypatch.setattr(service.graph.deepseek, "review_markdown", fake_review_markdown)
    monkeypatch.setattr(service.graph.deepseek, "improve_markdown", fake_improve_markdown)

    thread = await service.create_thread()
    await service.ingest_message(
        thread.thread_id,
        "我要做一节企业内训课，主题是提示词设计，学员是运营团队，目标是能写出稳定提示词，时长90分钟，限制是要基于真实案例。",
        "default-user",
    )
    state = await service.store.get_thread(thread.thread_id)
    assert calls["review"] >= 2
    assert calls["improve"] == 1
    assert state.run_metadata["auto_optimization_loops"] == 1
    assert state.review_batches[-1].total_score == 8.6
