from __future__ import annotations

import re
from dataclasses import dataclass
from textwrap import dedent
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.audit.logger import AuditService, EventBroker
from app.core.schemas import (
    AuditEvent,
    DecisionItem,
    DraftArtifact,
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
from app.core.settings import Settings
from app.llm.deepseek_client import DeepSeekClient
from app.review.rubric import RUBRIC
from app.storage.thread_store import ThreadStore


REQUIREMENT_DEFS = [
    {
        "slot_id": "topic",
        "label": "课程主题",
        "prompt_hint": "这节课具体教什么，比如提示词写作、海报设计、数据分析。",
        "patterns": [r"主题是([^，。,]+)", r"课题是([^，。,]+)", r"课程主题是([^，。,]+)"],
    },
    {
        "slot_id": "audience",
        "label": "目标学员",
        "prompt_hint": "这门课是给谁学的，比如运营、新媒体、小白用户、设计师。",
        "patterns": [r"学员是([^，。,]+)", r"对象是([^，。,]+)", r"人群是([^，。,]+)"],
    },
    {
        "slot_id": "objective",
        "label": "课程目标",
        "prompt_hint": "学完后学员要能做成什么事，最好是一个可以验证的结果。",
        "patterns": [r"目标是([^，。,]+)", r"希望达到([^，。,]+)"],
    },
    {
        "slot_id": "duration",
        "label": "课程时长",
        "prompt_hint": "总时长是多少，比如 30 分钟、90 分钟、2 小时。",
        "patterns": [r"时长(?:是)?([^，。,]+)", r"课时(?:是)?([^，。,]+)"],
    },
    {
        "slot_id": "constraints",
        "label": "限制与要求",
        "prompt_hint": "是否要基于真实案例、指定工具、商业场景、口播风格等。",
        "patterns": [r"限制(?:是)?([^，。,]+)", r"约束(?:是)?([^，。,]+)", r"要求(?:是)?([^，。,]+)"],
    },
]


@dataclass
class CourseGraph:
    settings: Settings
    store: ThreadStore
    broker: EventBroker
    audit: AuditService
    deepseek: DeepSeekClient

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
        graph.add_node("auto_improve", self.auto_improve)
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
        graph.add_conditional_edges(
            "critique_score",
            self.route_after_critique_score,
            {"auto_improve": "auto_improve", "human_review_interrupt": "human_review_interrupt"},
        )
        graph.add_edge("auto_improve", "draft_assemble")
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
        content = "\n".join(message.content for message in state.messages if message.role == MessageRole.USER)
        summary_parts = []
        missing_requirements = []
        for requirement in REQUIREMENT_DEFS:
            slot = state.requirement_slots.get(requirement["slot_id"]) or RequirementSlot(
                slot_id=requirement["slot_id"],
                label=requirement["label"],
                prompt_hint=requirement["prompt_hint"],
            )
            if not slot.value:
                for pattern in requirement["patterns"]:
                    match = re.search(pattern, content)
                    if match:
                        slot.value = match.group(1).strip()
                        slot.confidence = 0.85
                        break
            if slot.value:
                slot.confirmed = True
                summary_parts.append(f"{slot.label}: {slot.value}")
            else:
                missing_requirements.append(
                    {
                        "slot_id": requirement["slot_id"],
                        "label": requirement["label"],
                        "prompt_hint": requirement["prompt_hint"],
                    }
                )
            state.requirement_slots[requirement["slot_id"]] = slot
        state.run_metadata["missing_requirements"] = missing_requirements
        state.run_metadata["slot_summary"] = "\n".join(summary_parts) or "暂无"
        return await self._save_state(
            state,
            "requirement_gap_check",
            "GRAPH_NODE_COMPLETED",
            {"missing_requirements": [item["slot_id"] for item in missing_requirements]},
        )

    def route_after_gap_check(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        return "clarify_question" if state.run_metadata.get("missing_requirements") else "decision_update"

    async def clarify_question(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        question = await self.deepseek.ask_clarification(
            {
                "slot_summary": state.run_metadata.get("slot_summary", "暂无"),
                "missing_requirements": state.run_metadata.get("missing_requirements", []),
            }
        )
        state.messages.append(MessageRecord(role=MessageRole.ASSISTANT, content=question))
        state.status = ThreadStatus.COLLECTING
        await self.broker.publish(
            state.thread_id,
            {"type": "assistant_message", "thread_id": state.thread_id, "payload": {"content": question}},
        )
        return await self._save_state(
            state,
            "clarify_question",
            "CLARIFICATION_REQUESTED",
            {"question": question[:160]},
        )

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
            {"title": f"{slots.get('topic') or '主题'}快速上手", "goal": "完成最小可用结果，建立动作路径。"},
            {"title": f"{slots.get('topic') or '主题'}复杂约束实战", "goal": "在更真实的业务条件下调整策略和输出。"},
            {"title": f"{slots.get('topic') or '主题'}复盘迁移", "goal": "总结方法并迁移到类似场景。"},
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
        state.run_metadata["generated_markdown"] = ""
        state.draft_artifact = DraftArtifact(version=0, markdown="", summary="生成中...")
        chunk_count = 0
        example_reference = (
            "课程内容应尽量像示例那样清楚分出课程描述、你将学会、任务描述、操作演示、跟练素材、学习收获，"
            "并且让步骤、提示词演变、案例目标都能直接用于教学。"
        )
        async for chunk in self.deepseek.stream_markdown(
            {
                "decision_summary": state.decision_summary,
                "slot_summary": state.run_metadata.get("slot_summary", ""),
                "source_summary": state.run_metadata.get("source_summary", ""),
                "slots": slots,
                "example_reference": example_reference,
            }
        ):
            chunk_count += 1
            state.run_metadata["generated_markdown"] += chunk
            state.draft_artifact.markdown = state.run_metadata["generated_markdown"]
            await self.broker.publish(
                state.thread_id,
                {"type": "token_stream", "thread_id": state.thread_id, "payload": {"content": chunk}},
            )
            if chunk_count % 4 == 0:
                await self._save_state(
                    state,
                    "script_generate",
                    "GRAPH_NODE_STREAMING",
                    {"markdown_length": len(state.run_metadata["generated_markdown"])},
                )
        return await self._save_state(
            state,
            "script_generate",
            "GRAPH_NODE_COMPLETED",
            {"markdown_length": len(state.run_metadata["generated_markdown"])},
        )

    async def draft_assemble(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        history = state.run_metadata.setdefault("artifact_history", [])
        next_version = (state.draft_artifact.version + 1) if state.draft_artifact and state.draft_artifact.version > 0 else 1
        if state.draft_artifact and state.draft_artifact.version > 0:
            history.append(state.draft_artifact.model_dump(mode="json"))
        artifact = DraftArtifact(
            version=next_version,
            markdown=state.run_metadata["generated_markdown"],
            summary="当前课程主稿已生成。",
            derived_from_feedback_ids=[item.suggestion_id for item in state.approved_feedback],
        )
        state.draft_artifact = artifact
        state.version_chain.append(VersionRecord(version=artifact.version, artifact_id=artifact.artifact_id))
        await self.broker.publish(
            state.thread_id,
            {"type": "artifact_updated", "thread_id": state.thread_id, "payload": artifact.model_dump(mode="json")},
        )
        return await self._save_state(
            state,
            "draft_assemble",
            "DRAFT_GENERATED",
            {"artifact_version": artifact.version},
        )

    async def critique_score(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        state.status = ThreadStatus.REVIEW_PENDING
        result = await self.deepseek.review_markdown(
            markdown=state.draft_artifact.markdown,
            rubric=RUBRIC,
            threshold=self.settings.default_review_threshold,
        )
        batch = ReviewBatch(
            draft_version=state.draft_artifact.version,
            total_score=float(result["total_score"]),
            criteria=[ReviewCriterionResult.model_validate(item) for item in result["criteria"]],
            suggestions=[ReviewSuggestion.model_validate(item) for item in result["suggestions"]],
            threshold=self.settings.default_review_threshold,
        )
        state.review_batches.append(batch)
        await self.broker.publish(
            state.thread_id,
            {"type": "review_batch", "thread_id": state.thread_id, "payload": batch.model_dump(mode="json")},
        )
        return await self._save_state(
            state,
            "critique_score",
            "REVIEW_BATCH_CREATED",
            {"review_batch_id": batch.review_batch_id, "score": batch.total_score},
        )

    def route_after_critique_score(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        latest_review = state.review_batches[-1]
        loops = state.run_metadata.get("auto_optimization_loops", 0)
        if latest_review.total_score < self.settings.default_review_threshold and loops < self.settings.max_auto_optimization_loops:
            return "auto_improve"
        return "human_review_interrupt"

    async def auto_improve(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        latest_review = state.review_batches[-1]
        state.status = ThreadStatus.REVISING
        loops = int(state.run_metadata.get("auto_optimization_loops", 0)) + 1
        state.run_metadata["auto_optimization_loops"] = loops
        approved_changes = [suggestion.suggestion for suggestion in latest_review.suggestions]
        state.run_metadata["generated_markdown"] = await self.deepseek.improve_markdown(
            markdown=state.draft_artifact.markdown,
            approved_changes=approved_changes,
            context_summary=state.decision_summary,
        )
        return await self._save_state(
            state,
            "auto_improve",
            "DRAFT_REVISED",
            {"mode": "auto", "loop": loops, "pending_review_score": latest_review.total_score},
        )

    async def human_review_interrupt(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        batch = state.review_batches[-1]
        await self.store.save_thread(state)
        resume_value = interrupt(
            InterruptPayload(
                review_batch_id=batch.review_batch_id,
                draft_version=batch.draft_version,
                total_score=batch.total_score,
                criteria=batch.criteria,
                suggestions=batch.suggestions,
            ).model_dump(mode="json")
        )
        state.run_metadata["resume_payload"] = resume_value
        return await self._save_state(
            state,
            "human_review_interrupt",
            "REVIEW_INTERRUPTED",
            {"review_batch_id": batch.review_batch_id},
        )

    async def approved_feedback_merge(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        payload = state.run_metadata.get("resume_payload", {})
        state.approved_feedback = payload.get("review_actions", [])
        return await self._save_state(
            state,
            "approved_feedback_merge",
            "GRAPH_NODE_COMPLETED",
            {"approved_count": len(state.approved_feedback)},
        )

    async def revise_draft(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        if not state.approved_feedback:
            return await self._save_state(
                state,
                "revise_draft",
                "GRAPH_NODE_SKIPPED",
                {"reason": "no_approved_feedback"},
            )

        instructions = []
        for action in state.approved_feedback:
            if action["action"] == "reject":
                continue
            instructions.append(action.get("edited_suggestion") or "根据人工确认意见补强对应段落。")
        state.run_metadata["generated_markdown"] = await self.deepseek.improve_markdown(
            markdown=state.draft_artifact.markdown,
            approved_changes=instructions,
            context_summary=state.decision_summary,
        )
        return await self.draft_assemble({"thread_id": state.thread_id, "state": state.model_dump(mode="json")})

    async def completion_gate(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        latest_review = state.review_batches[-1]
        has_non_rejected_actions = any(action["action"] != "reject" for action in state.approved_feedback)
        if latest_review.total_score >= self.settings.default_review_threshold and has_non_rejected_actions:
            state.status = ThreadStatus.COMPLETED
        else:
            state.status = ThreadStatus.REVIEW_PENDING
        return await self._save_state(
            state,
            "completion_gate",
            "THREAD_COMPLETED" if state.status == ThreadStatus.COMPLETED else "GRAPH_NODE_COMPLETED",
            {"status": state.status, "score": latest_review.total_score},
        )

    def route_after_completion_gate(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        return "completed" if state.status == ThreadStatus.COMPLETED else "critique_score"
