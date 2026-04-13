from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.deps import get_service
from app.core.schemas import ApiEnvelope, SendMessageRequest
from app.services.course_agent import CourseAgentService


router = APIRouter()


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
