from __future__ import annotations

from dataclasses import dataclass
from textwrap import dedent
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.audit.logger import AuditService, EventBroker
from app.config import Settings
from app.models.llm import ModelGateway
from app.persistence.store import ThreadStore
from app.review.rubric import RUBRIC, RUBRIC_VERSION
from app.schemas import (
    AuditEvent,
    DecisionItem,
    DraftArtifact,
    HumanReviewAction,
    InterruptPayload,
    MessageRecord,
    MessageRole,
    RequirementSlot,
    ReviewBatch,
    ReviewCriterionResult,
    ReviewSuggestion,
    ThreadState,
    ThreadStatus,
    VersionRecord,
)


REQUIRED_SLOTS = ["topic", "audience", "objective", "duration", "constraints"]


@dataclass
class CourseGraph:
    settings: Settings
    store: ThreadStore
    broker: EventBroker
    audit: AuditService
    model_gateway: ModelGateway

    def __post_init__(self) -> None:
        self.checkpointer = MemorySaver()
        self.graph = self._build_graph().compile(checkpointer=self.checkpointer)

    def _build_graph(self):
        graph = StateGraph(dict)
        graph.add_node("intake_message", self.intake_message)
        graph.add_node("requirement_gap_check", self.requirement_gap_check)
        graph.add_node("clarify_question", self.clarify_question)
        graph.add_node("decision_update", self.decision_update)
        graph.add_node("source_parse", self.source_parse)
        graph.add_node("outline_generate", self.outline_generate)
        graph.add_node("case_design_generate", self.case_design_generate)
        graph.add_node("script_generate", self.script_generate)
        graph.add_node("draft_assemble", self.draft_assemble)
        graph.add_node("critique_score", self.critique_score)
        graph.add_node("human_review_interrupt", self.human_review_interrupt)
        graph.add_node("approved_feedback_merge", self.approved_feedback_merge)
        graph.add_node("revise_draft", self.revise_draft)
        graph.add_node("completion_gate", self.completion_gate)
        graph.add_edge(START, "intake_message")
        graph.add_edge("intake_message", "requirement_gap_check")
        graph.add_conditional_edges(
            "requirement_gap_check",
            self.route_after_gap_check,
            {"clarify_question": "clarify_question", "decision_update": "decision_update"},
        )
        graph.add_edge("clarify_question", END)
        graph.add_edge("decision_update", "source_parse")
        graph.add_edge("source_parse", "outline_generate")
        graph.add_edge("outline_generate", "case_design_generate")
        graph.add_edge("case_design_generate", "script_generate")
        graph.add_edge("script_generate", "draft_assemble")
        graph.add_edge("draft_assemble", "critique_score")
        graph.add_edge("critique_score", "human_review_interrupt")
        graph.add_edge("human_review_interrupt", "approved_feedback_merge")
        graph.add_edge("approved_feedback_merge", "revise_draft")
        graph.add_edge("revise_draft", "completion_gate")
        graph.add_conditional_edges(
            "completion_gate",
            self.route_after_completion_gate,
            {"completed": END, "critique_score": "critique_score"},
        )
        return graph

    async def run_thread(self, thread_id: str) -> None:
        state = await self.store.get_thread(thread_id)
        config = {"configurable": {"thread_id": thread_id}}
        await self.graph.ainvoke({"thread_id": thread_id, "resume": False, "state": state.model_dump(mode="json")}, config=config)

    async def resume_thread(self, thread_id: str, resume_value: dict[str, Any]) -> None:
        config = {"configurable": {"thread_id": thread_id}}
        await self.graph.ainvoke(Command(resume=resume_value), config=config)

    async def _load_state(self, raw_state: dict[str, Any]) -> ThreadState:
        if raw_state.get("state"):
            return ThreadState.model_validate(raw_state["state"])
        return await self.store.get_thread(raw_state["thread_id"])

    async def _save_state(self, state: ThreadState, node_name: str, event_type: str, payload_summary: dict[str, Any]) -> dict[str, Any]:
        await self.store.save_thread(state)
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                user_id=state.user_id,
                node_name=node_name,
                event_type=event_type,
                artifact_version=state.draft_artifact.version if state.draft_artifact else None,
                payload_summary=payload_summary,
            )
        )
        await self.broker.publish(
            state.thread_id,
            {
                "type": "node_update",
                "thread_id": state.thread_id,
                "payload": {"node_name": node_name, "status": state.status, **payload_summary},
            },
        )
        return {"thread_id": state.thread_id, "state": state.model_dump(mode="json")}

    async def intake_message(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        return await self._save_state(state, "intake_message", "GRAPH_NODE_ENTERED", {"message_count": len(state.messages)})

    async def requirement_gap_check(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        summary_parts = []
        content = "\n".join(message.content for message in state.messages if message.role == MessageRole.USER)
        field_map = {
            "topic": ["主题", "课题", "课程"],
            "audience": ["学员", "对象", "人群"],
            "objective": ["目标", "想解决", "希望达到"],
            "duration": ["时长", "分钟", "小时"],
            "constraints": ["限制", "约束", "注意", "要求"],
        }
        for slot_id, keywords in field_map.items():
            slot = state.requirement_slots.get(slot_id) or RequirementSlot(slot_id=slot_id)
            if not slot.value:
                for keyword in keywords:
                    if keyword in content:
                        slot.value = content
                        slot.confidence = 0.5
                        break
            if slot.value:
                slot.confirmed = True
                summary_parts.append(f"{slot_id}: {slot.value[:80]}")
            state.requirement_slots[slot_id] = slot
        state.run_metadata["missing_slots"] = [
            slot_id for slot_id in REQUIRED_SLOTS if not state.requirement_slots[slot_id].value
        ]
        state.run_metadata["slot_summary"] = "\n".join(summary_parts) or "暂无"
        return await self._save_state(
            state,
            "requirement_gap_check",
            "GRAPH_NODE_COMPLETED",
            {"missing_slots": state.run_metadata["missing_slots"]},
        )

    def route_after_gap_check(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        return "clarify_question" if state.run_metadata.get("missing_slots") else "decision_update"

    async def clarify_question(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        question = await self.model_gateway.ask_clarification(
            {
                "slot_summary": state.run_metadata.get("slot_summary", "暂无"),
                "missing_slots": state.run_metadata.get("missing_slots", []),
            }
        )
        state.messages.append(MessageRecord(role=MessageRole.ASSISTANT, content=question))
        state.status = ThreadStatus.COLLECTING
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                user_id=state.user_id,
                event_type="CLARIFICATION_REQUESTED",
                payload_summary={"question": question[:200]},
            )
        )
        await self.broker.publish(
            state.thread_id,
            {"type": "assistant_message", "thread_id": state.thread_id, "payload": {"content": question}},
        )
        return {"thread_id": state.thread_id, "state": state.model_dump(mode="json")}

    async def decision_update(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        for slot in state.requirement_slots.values():
            if slot.value and slot.confirmed:
                if not any(item.topic == slot.slot_id and item.value == slot.value for item in state.decision_ledger):
                    state.decision_ledger.append(
                        DecisionItem(topic=slot.slot_id, value=slot.value, reason="从用户对话中确认")
                    )
        state.decision_summary = "\n".join(f"{item.topic}: {item.value}" for item in state.decision_ledger)
        return await self._save_state(
            state,
            "decision_update",
            "DECISION_CONFIRMED",
            {"decision_count": len(state.decision_ledger)},
        )

    async def source_parse(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        source_summary = []
        for document in state.source_manifest:
            if document.extract_status == "parsed":
                preview = " ".join(chunk.text for chunk in document.text_chunks[:2])
                source_summary.append(f"{document.filename}: {preview[:200]}")
        state.run_metadata["source_summary"] = "\n".join(source_summary) or "无上传资料"
        return await self._save_state(
            state,
            "source_parse",
            "GRAPH_NODE_COMPLETED",
            {"source_count": len(state.source_manifest)},
        )

    async def outline_generate(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        state.status = ThreadStatus.GENERATING
        slots = {key: value.value or "" for key, value in state.requirement_slots.items()}
        outline = dedent(
            f"""
            核心问题：围绕“{slots.get('topic') or '课程主题'}”建立单课解决路径。
            课程对象：{slots.get('audience') or '企业学员'}
            课程目标：{slots.get('objective') or '完成业务目标'}
            """
        ).strip()
        state.run_metadata["outline"] = outline
        return await self._save_state(
            state,
            "outline_generate",
            "GRAPH_NODE_COMPLETED",
            {"outline_preview": outline[:160]},
        )

    async def case_design_generate(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        slots = {key: value.value or "" for key, value in state.requirement_slots.items()}
        cases = [
            {
                "title": f"{slots.get('topic') or '主题'}快速上手",
                "goal": "完成最小可用结果，建立动作路径。",
            },
            {
                "title": f"{slots.get('topic') or '主题'}复杂约束实战",
                "goal": "在更真实的业务条件下调整策略和输出。",
            },
            {
                "title": f"{slots.get('topic') or '主题'}复盘迁移",
                "goal": "总结方法并迁移到类似场景。",
            },
        ]
        state.run_metadata["cases"] = cases
        return await self._save_state(
            state,
            "case_design_generate",
            "GRAPH_NODE_COMPLETED",
            {"case_count": len(cases)},
        )

    async def script_generate(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        slots = {key: value.value or "" for key, value in state.requirement_slots.items()}
        markdown = await self.model_gateway.generate_markdown(
            {
                "decision_summary": state.decision_summary,
                "slot_summary": state.run_metadata.get("slot_summary", ""),
                "source_summary": state.run_metadata.get("source_summary", ""),
                "slots": slots,
            }
        )
        state.run_metadata["generated_markdown"] = markdown
        await self.broker.publish(
            state.thread_id,
            {
                "type": "token_stream",
                "thread_id": state.thread_id,
                "payload": {"content": markdown},
            },
        )
        return await self._save_state(
            state,
            "script_generate",
            "GRAPH_NODE_COMPLETED",
            {"markdown_length": len(markdown)},
        )

    async def draft_assemble(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        history = state.run_metadata.setdefault("artifact_history", [])
        next_version = (state.draft_artifact.version + 1) if state.draft_artifact else 1
        if state.draft_artifact:
            history.append(state.draft_artifact.model_dump(mode="json"))
        artifact = DraftArtifact(
            version=next_version,
            markdown=state.run_metadata["generated_markdown"],
            summary="当前课程主稿已生成。",
            derived_from_feedback_ids=[item.suggestion_id for item in state.approved_feedback],
        )
        state.draft_artifact = artifact
        state.version_chain.append(VersionRecord(version=artifact.version, artifact_id=artifact.artifact_id))
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                event_type="DRAFT_GENERATED",
                artifact_version=artifact.version,
                payload_summary={"summary": artifact.summary},
            )
        )
        await self.broker.publish(
            state.thread_id,
            {"type": "artifact_updated", "thread_id": state.thread_id, "payload": artifact.model_dump(mode="json")},
        )
        return {"thread_id": state.thread_id, "state": state.model_dump(mode="json")}

    async def critique_score(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        state.status = ThreadStatus.REVIEW_PENDING
        result = await self.model_gateway.review_markdown(
            markdown=state.draft_artifact.markdown,
            rubric=RUBRIC,
            threshold=self.settings.default_review_threshold,
        )
        criteria = [ReviewCriterionResult.model_validate(item) for item in result["criteria"]]
        suggestions = [ReviewSuggestion.model_validate(item) for item in result["suggestions"]]
        batch = ReviewBatch(
            draft_version=state.draft_artifact.version,
            total_score=float(result["total_score"]),
            criteria=criteria,
            suggestions=suggestions,
            threshold=self.settings.default_review_threshold,
        )
        state.review_batches.append(batch)
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                event_type="REVIEW_BATCH_CREATED",
                artifact_version=state.draft_artifact.version,
                payload_summary={"review_batch_id": batch.review_batch_id, "score": batch.total_score},
            )
        )
        await self.broker.publish(
            state.thread_id,
            {
                "type": "review_batch",
                "thread_id": state.thread_id,
                "payload": batch.model_dump(mode="json"),
            },
        )
        return {"thread_id": state.thread_id, "state": state.model_dump(mode="json")}

    async def human_review_interrupt(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        batch = state.review_batches[-1]
        payload = InterruptPayload(
            review_batch_id=batch.review_batch_id,
            draft_version=batch.draft_version,
            total_score=batch.total_score,
            criteria=batch.criteria,
            suggestions=batch.suggestions,
        )
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                event_type="REVIEW_INTERRUPTED",
                artifact_version=batch.draft_version,
                payload_summary={"review_batch_id": batch.review_batch_id},
            )
        )
        resume_value = interrupt(payload.model_dump(mode="json"))
        state.run_metadata["resume_payload"] = resume_value
        return {"thread_id": state.thread_id, "state": state.model_dump(mode="json")}

    async def approved_feedback_merge(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        payload = state.run_metadata.get("resume_payload", {})
        actions = [
            HumanReviewAction.model_validate(item)
            for item in payload.get("review_actions", [])
        ]
        state.approved_feedback = actions
        return await self._save_state(
            state,
            "approved_feedback_merge",
            "GRAPH_NODE_COMPLETED",
            {"approved_count": len(actions)},
        )

    async def revise_draft(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        if not state.approved_feedback:
            return {"thread_id": state.thread_id, "state": state.model_dump(mode="json")}

        revised = state.draft_artifact.markdown
        approved = []
        for action in state.approved_feedback:
            if action.action.value == "reject":
                continue
            approved.append(action.suggestion_id)
            instruction = action.edited_suggestion or "根据评审意见补强对应段落。"
            revised = revised + f"\n\n> 修订说明：{instruction}"
        state.run_metadata["generated_markdown"] = revised
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                event_type="DRAFT_REVISED",
                artifact_version=state.draft_artifact.version,
                payload_summary={"feedback_ids": approved},
            )
        )
        return await self.draft_assemble({"thread_id": state.thread_id, "state": state.model_dump(mode="json")})

    async def completion_gate(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        latest_review = state.review_batches[-1]
        has_non_rejected_actions = any(action.action.value != "reject" for action in state.approved_feedback)
        if latest_review.total_score >= self.settings.default_review_threshold and has_non_rejected_actions:
            state.status = ThreadStatus.COMPLETED
            await self.audit.record(
                AuditEvent(
                    thread_id=state.thread_id,
                    event_type="THREAD_COMPLETED",
                    artifact_version=state.draft_artifact.version,
                    payload_summary={"score": latest_review.total_score},
                )
            )
        else:
            state.status = ThreadStatus.REVIEW_PENDING
        return {"thread_id": state.thread_id, "state": state.model_dump(mode="json")}

    def route_after_completion_gate(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        return "completed" if state.status == ThreadStatus.COMPLETED else "critique_score"
