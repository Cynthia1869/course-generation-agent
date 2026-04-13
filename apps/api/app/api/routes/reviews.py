from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.deps import get_service
from app.core.schemas import ApiEnvelope, ReviewSubmitRequest
from app.services.course_agent import CourseAgentService


router = APIRouter()


def envelope(*, data: dict, thread_id: str | None = None, request_id: str | None = None) -> ApiEnvelope:
    return ApiEnvelope(request_id=request_id or uuid4().hex, thread_id=thread_id, data=data)


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
