from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_service
from app.core.schemas import ApiEnvelope, SendMessageRequest
from app.services.course_agent import CourseAgentService
from app.storage.thread_store import ThreadNotFoundError


router = APIRouter()


def envelope(*, data: dict, thread_id: str | None = None, request_id: str | None = None) -> ApiEnvelope:
    return ApiEnvelope(request_id=request_id or uuid4().hex, thread_id=thread_id, data=data)


@router.post("/threads")
async def create_thread(service: CourseAgentService = Depends(get_service)):
    thread = await service.create_thread()
    return envelope(data={"thread": thread.model_dump(mode="json")}, thread_id=thread.thread_id)


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str, service: CourseAgentService = Depends(get_service)):
    try:
        summary = await service.store.build_summary(thread_id)
        state = await service.store.get_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(
        thread_id=thread_id,
        data={
            "thread": summary.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
        },
    )


@router.get("/threads/{thread_id}/history")
async def get_thread_history(thread_id: str, service: CourseAgentService = Depends(get_service)):
    try:
        history = await service.get_history(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"history": [item.model_dump(mode="json") for item in history]})


@router.post("/threads/{thread_id}/pause")
async def pause_thread(thread_id: str, service: CourseAgentService = Depends(get_service)):
    try:
        await service.pause_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"paused": True})


@router.post("/threads/{thread_id}/resume")
async def resume_thread(thread_id: str, service: CourseAgentService = Depends(get_service)):
    try:
        await service.resume_paused_thread(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"resumed": True})


@router.delete("/threads/{thread_id}/messages/last")
async def retract_last_message(thread_id: str, service: CourseAgentService = Depends(get_service)):
    try:
        await service.retract_last_message(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"retracted": True})


@router.post("/threads/{thread_id}/messages")
async def send_message(
    thread_id: str,
    request: SendMessageRequest,
    service: CourseAgentService = Depends(get_service),
):
    try:
        await service.ingest_message(thread_id, request.content, request.user_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"accepted": True})
