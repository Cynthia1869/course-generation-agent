from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_service
from app.core.schemas import ApiEnvelope
from app.services.course_agent import CourseAgentService


router = APIRouter()


def envelope(*, data: dict, thread_id: str | None = None, request_id: str | None = None) -> ApiEnvelope:
    return ApiEnvelope(request_id=request_id or uuid4().hex, thread_id=thread_id, data=data)


@router.get("/threads/{thread_id}/stream")
async def thread_stream(thread_id: str, service: CourseAgentService = Depends(get_service)):
    queue = service.broker.subscribe(thread_id)

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                yield {
                    "event": event["type"],
                    "data": json.dumps(event["payload"], ensure_ascii=False),
                }
        except asyncio.CancelledError:
            raise
        finally:
            service.broker.unsubscribe(thread_id, queue)

    return EventSourceResponse(event_generator())


@router.get("/threads/{thread_id}/events")
async def list_events(thread_id: str, service: CourseAgentService = Depends(get_service)):
    events = service.audit.list_events(thread_id)
    return envelope(thread_id=thread_id, data={"events": [event.model_dump(mode="json") for event in events]})
