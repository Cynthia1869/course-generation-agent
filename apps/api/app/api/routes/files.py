from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_service
from app.core.schemas import ApiEnvelope
from app.services.course_agent import CourseAgentService
from app.storage.thread_store import ThreadNotFoundError


router = APIRouter()


def envelope(*, data: dict, thread_id: str | None = None, request_id: str | None = None) -> ApiEnvelope:
    return ApiEnvelope(request_id=request_id or uuid4().hex, thread_id=thread_id, data=data)


@router.post("/threads/{thread_id}/files")
async def upload_file(
    thread_id: str,
    file: UploadFile = File(...),
    service: CourseAgentService = Depends(get_service),
):
    try:
        content = await file.read()
        await service.upload_file(thread_id, file.filename, file.content_type or "application/octet-stream", content)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"uploaded": True, "filename": file.filename})


@router.get("/threads/{thread_id}/files")
async def list_files(thread_id: str, service: CourseAgentService = Depends(get_service)):
    try:
        files = await service.store.list_files(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"files": [file.model_dump(mode="json") for file in files]})


@router.get("/threads/{thread_id}/artifacts/latest")
async def latest_artifact(thread_id: str, service: CourseAgentService = Depends(get_service)):
    try:
        artifact = await service.store.latest_artifact(thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"artifact": artifact.model_dump(mode="json") if artifact else None})


@router.get("/threads/{thread_id}/artifacts/{version}/diff/{prev_version}")
async def artifact_diff(thread_id: str, version: int, prev_version: int, service: CourseAgentService = Depends(get_service)):
    try:
        diff = await service.store.diff_versions(thread_id, version, prev_version)
    except ThreadNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "thread_not_found", "message": f"Thread not found: {exc.thread_id}"},
        ) from exc
    return envelope(thread_id=thread_id, data={"diff": diff, "version": version, "prev_version": prev_version})
