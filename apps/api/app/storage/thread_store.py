from __future__ import annotations

import asyncio
from difflib import unified_diff
from uuid import uuid4

from app.core.schemas import DraftArtifact, ReviewBatch, SourceDocument, ThreadState, ThreadStatus, ThreadSummary


class ThreadStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._threads: dict[str, ThreadState] = {}

    async def create_thread(self, *, user_id: str = "default-user") -> ThreadState:
        async with self._lock:
            thread_id = uuid4().hex
            state = ThreadState(thread_id=thread_id, user_id=user_id)
            self._threads[thread_id] = state
            return state

    async def get_thread(self, thread_id: str) -> ThreadState:
        async with self._lock:
            return self._threads[thread_id]

    async def save_thread(self, state: ThreadState) -> ThreadState:
        async with self._lock:
            self._threads[state.thread_id] = state
            return state

    async def list_files(self, thread_id: str) -> list[SourceDocument]:
        state = await self.get_thread(thread_id)
        return state.source_manifest

    async def latest_artifact(self, thread_id: str) -> DraftArtifact | None:
        state = await self.get_thread(thread_id)
        return state.draft_artifact

    async def get_review_batch(self, thread_id: str, review_batch_id: str) -> ReviewBatch:
        state = await self.get_thread(thread_id)
        for batch in state.review_batches:
            if batch.review_batch_id == review_batch_id:
                return batch
        raise KeyError(f"Review batch not found: {review_batch_id}")

    async def build_summary(self, thread_id: str) -> ThreadSummary:
        state = await self.get_thread(thread_id)
        latest_review = state.review_batches[-1] if state.review_batches else None
        latest_version = state.draft_artifact.version if state.draft_artifact else None
        return ThreadSummary(
            thread_id=thread_id,
            user_id=state.user_id,
            status=state.status,
            latest_artifact_version=latest_version,
            review_pending=state.status == ThreadStatus.REVIEW_PENDING,
            latest_score=latest_review.total_score if latest_review else None,
        )

    async def diff_versions(self, thread_id: str, version: int, prev_version: int) -> str:
        state = await self.get_thread(thread_id)
        versions: dict[int, DraftArtifact] = {}
        if state.draft_artifact:
            versions[state.draft_artifact.version] = state.draft_artifact
        history = state.run_metadata.get("artifact_history", [])
        for item in history:
            artifact = DraftArtifact.model_validate(item)
            versions[artifact.version] = artifact
        current = versions[version]
        previous = versions[prev_version]
        diff = unified_diff(
            previous.markdown.splitlines(),
            current.markdown.splitlines(),
            fromfile=f"v{prev_version}",
            tofile=f"v{version}",
            lineterm="",
        )
        return "\n".join(diff)
