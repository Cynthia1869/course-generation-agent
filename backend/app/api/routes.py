from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse

from app.api.deps import get_service
from app.application.services import CourseAgentService
from app.schemas import ApiEnvelope, ReviewSubmitRequest, SendMessageRequest


router = APIRouter(prefix="/api/v1")


def envelope(*, data: dict, thread_id: str | None = None, request_id: str | None = None) -> ApiEnvelope:
    return ApiEnvelope(request_id=request_id or uuid4().hex, thread_id=thread_id, data=data)


@router.post("/threads")
async def create_thread(service: CourseAgentService = Depends(get_service)):
    thread = await service.create_thread()
    return envelope(data={"thread": thread.model_dump(mode="json")}, thread_id=thread.thread_id)


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str, service: CourseAgentService = Depends(get_service)):
    summary = await service.store.build_summary(thread_id)
    state = await service.store.get_thread(thread_id)
    return envelope(
        thread_id=thread_id,
        data={
            "thread": summary.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
        },
    )


@router.post("/threads/{thread_id}/messages")
async def send_message(
    thread_id: str,
    request: SendMessageRequest,
    service: CourseAgentService = Depends(get_service),
):
    await service.ingest_message(thread_id, request.content, request.user_id)
    return envelope(thread_id=thread_id, data={"accepted": True})


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


@router.post("/threads/{thread_id}/files")
async def upload_file(
    thread_id: str,
    file: UploadFile = File(...),
    service: CourseAgentService = Depends(get_service),
):
    content = await file.read()
    await service.upload_file(thread_id, file.filename, file.content_type or "application/octet-stream", content)
    return envelope(thread_id=thread_id, data={"uploaded": True, "filename": file.filename})


@router.get("/threads/{thread_id}/files")
async def list_files(thread_id: str, service: CourseAgentService = Depends(get_service)):
    files = await service.store.list_files(thread_id)
    return envelope(thread_id=thread_id, data={"files": [file.model_dump(mode="json") for file in files]})


@router.get("/threads/{thread_id}/artifacts/latest")
async def latest_artifact(thread_id: str, service: CourseAgentService = Depends(get_service)):
    artifact = await service.store.latest_artifact(thread_id)
    return envelope(thread_id=thread_id, data={"artifact": artifact.model_dump(mode="json") if artifact else None})


@router.get("/threads/{thread_id}/artifacts/{version}/diff/{prev_version}")
async def artifact_diff(thread_id: str, version: int, prev_version: int, service: CourseAgentService = Depends(get_service)):
    diff = await service.store.diff_versions(thread_id, version, prev_version)
    return envelope(thread_id=thread_id, data={"diff": diff, "version": version, "prev_version": prev_version})


@router.get("/threads/{thread_id}/review-batches/{batch_id}")
async def get_review_batch(thread_id: str, batch_id: str, service: CourseAgentService = Depends(get_service)):
    batch = await service.store.get_review_batch(thread_id, batch_id)
    return envelope(thread_id=thread_id, data={"review_batch": batch.model_dump(mode="json")})


@router.post("/threads/{thread_id}/review-batches/{batch_id}/submit")
async def submit_review(
    thread_id: str,
    batch_id: str,
    request: ReviewSubmitRequest,
    service: CourseAgentService = Depends(get_service),
):
    await service.submit_review(thread_id, batch_id, request)
    return envelope(thread_id=thread_id, data={"submitted": True, "review_batch_id": batch_id})


@router.get("/threads/{thread_id}/events")
async def list_events(thread_id: str, service: CourseAgentService = Depends(get_service)):
    events = service.audit.list_events(thread_id)
    return envelope(thread_id=thread_id, data={"events": [event.model_dump(mode="json") for event in events]})
