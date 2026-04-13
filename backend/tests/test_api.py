import os

import pytest
from httpx import ASGITransport, AsyncClient

os.environ["APP_ENV"] = "test"

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

        await client.get(f"/api/v1/threads/{thread_id}")
