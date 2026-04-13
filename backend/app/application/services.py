from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from langgraph.types import Command

from app.audit.logger import AuditService, EventBroker
from app.config import Settings
from app.documents.parser import DocumentParser
from app.graph.workflow import CourseGraph
from app.persistence.store import ThreadStore
from app.schemas import (
    ApiEnvelope,
    AuditEvent,
    MessageRecord,
    MessageRole,
    ReviewSubmitRequest,
    ThreadStatus,
)


class CourseAgentService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: ThreadStore,
        broker: EventBroker,
        audit: AuditService,
        parser: DocumentParser,
        graph: CourseGraph,
    ) -> None:
        self.settings = settings
        self.store = store
        self.broker = broker
        self.audit = audit
        self.parser = parser
        self.graph = graph

    async def create_thread(self, user_id: str = "default-user"):
        state = await self.store.create_thread(user_id=user_id)
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                user_id=user_id,
                event_type="THREAD_CREATED",
                payload_summary={"status": state.status},
            )
        )
        return await self.store.build_summary(state.thread_id)

    async def ingest_message(self, thread_id: str, content: str, user_id: str) -> None:
        state = await self.store.get_thread(thread_id)
        state.messages.append(MessageRecord(role=MessageRole.USER, content=content))
        state.status = ThreadStatus.COLLECTING
        await self.store.save_thread(state)
        await self.audit.record(
            AuditEvent(
                thread_id=thread_id,
                user_id=user_id,
                event_type="MESSAGE_RECEIVED",
                payload_summary={"content_preview": content[:120]},
            )
        )
        await self.broker.publish(
            thread_id,
            {
                "type": "user_message",
                "thread_id": thread_id,
                "payload": {"content": content},
            },
        )
        if self.settings.app_env == "test":
            await self.graph.run_thread(thread_id)
        else:
            asyncio.create_task(self.graph.run_thread(thread_id))

    async def upload_file(self, thread_id: str, filename: str, mime_type: str, content: bytes) -> None:
        path = self.settings.storage_dir / thread_id
        path.mkdir(parents=True, exist_ok=True)
        file_path = path / filename
        file_path.write_bytes(content)
        doc = self.parser.parse_file(file_path, mime_type)
        state = await self.store.get_thread(thread_id)
        state.source_manifest.append(doc)
        await self.store.save_thread(state)
        await self.audit.record(
            AuditEvent(
                thread_id=thread_id,
                event_type="FILE_UPLOADED",
                artifact_version=state.draft_artifact.version if state.draft_artifact else None,
                payload_summary={"filename": filename, "status": doc.extract_status},
            )
        )
        await self.audit.record(
            AuditEvent(
                thread_id=thread_id,
                event_type="FILE_PARSED",
                payload_summary={"filename": filename, "chunks": len(doc.text_chunks)},
            )
        )
        await self.broker.publish(
            thread_id,
            {
                "type": "file_uploaded",
                "thread_id": thread_id,
                "payload": doc.model_dump(mode="json"),
            },
        )

    async def submit_review(self, thread_id: str, batch_id: str, review_request: ReviewSubmitRequest) -> None:
        state = await self.store.get_thread(thread_id)
        state.approved_feedback = review_request.review_actions
        state.status = ThreadStatus.REVISING
        await self.store.save_thread(state)
        await self.audit.record(
            AuditEvent(
                thread_id=thread_id,
                user_id=review_request.submitter_id,
                event_type="REVIEW_ACTION_SUBMITTED",
                payload_summary={
                    "review_batch_id": batch_id,
                    "actions": [item.action for item in review_request.review_actions],
                },
            )
        )
        resume_value = {
            "review_batch_id": batch_id,
            "review_actions": [item.model_dump(mode="json") for item in review_request.review_actions],
            "submitter_id": review_request.submitter_id,
        }
        if self.settings.app_env == "test":
            await self.graph.resume_thread(thread_id, resume_value)
        else:
            asyncio.create_task(self.graph.resume_thread(thread_id, resume_value))
