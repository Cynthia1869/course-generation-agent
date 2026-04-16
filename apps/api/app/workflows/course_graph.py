from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.audit.logger import AuditService, EventBroker
from app.core.schemas import (
    AuditEvent,
    ConstraintKind,
    ConfirmedArtifactContext,
    ConversationConstraint,
    CourseMode,
    DecisionItem,
    DraftArtifact,
    GenerationRun,
    GenerationRunKind,
    GenerationRunStatus,
    GenerationSessionState,
    InterruptPayload,
    LLMProviderConfig,
    MessageRecord,
    MessageRole,
    PromptContextLayers,
    RequirementSlot,
    ResumePayload,
    ReviewBatch,
    ReviewCriterionResult,
    ReviewSuggestion,
    SavedArtifactRecord,
    StepArtifactRecord,
    StepArtifactStatus,
    StepStructuredInput,
    StepStructuredSlot,
    StepStatus,
    ThreadHistoryEntry,
    ThreadState,
    ThreadStatus,
    TimelineEvent,
    VersionRecord,
)
from app.core.settings import Settings
from app.core.step_catalog import SLOT_DEFINITIONS, StepBlueprint, get_slot_definition, get_step_blueprint
from app.llm.deepseek_client import DeepSeekClient
from app.review.rubric import RUBRIC
from app.series.decision_scoring import score_series_framework_markdown
from app.series.questionnaire import QUESTION_FLOW, get_question_by_step, parse_user_answer, render_question_prompt
from app.series.scoring import parse_framework_markdown
from app.storage.thread_store import ThreadStore


CONFIRMATION_PATTERNS = [
    r"^(开始生成|开始吧|可以生成|生成吧|就按这个来|没问题|可以|行|好的|确认|开始|继续下一步)$",
    r"(开始生成|可以生成|就按这个来|确认开始|继续下一步)",
]

SERIES_STARTER_PROMPT = (
    "请选择使用方式：\n"
    "A. 我没有框架，直接开始制课\n"
    "B. 我有现成的框架，直接评分并优化"
)


@dataclass
class CourseGraph:
    settings: Settings
    store: ThreadStore
    broker: EventBroker
    audit: AuditService
    deepseek: DeepSeekClient
    _graph: Any | None = field(default=None, init=False)
    _graph_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _checkpointer: AsyncSqliteSaver | None = field(default=None, init=False)

    @property
    def checkpoint_db_path(self) -> str:
        return str(self.settings.storage_dir / "langgraph_checkpoints.sqlite")

    async def _ensure_graph(self) -> Any:
        if self._graph is not None:
            return self._graph
        async with self._graph_lock:
            if self._graph is not None:
                return self._graph
            import aiosqlite

            conn = await aiosqlite.connect(self.checkpoint_db_path)
            self._checkpointer = AsyncSqliteSaver(conn)
            await self._checkpointer.setup()
            self._graph = self._build_graph().compile(checkpointer=self._checkpointer)
            return self._graph

    async def run_thread(self, thread_id: str) -> None:
        graph = await self._ensure_graph()
        try:
            state = await self.store.get_thread(thread_id)
            config = {"configurable": {"thread_id": thread_id}}
            await graph.ainvoke({"thread_id": thread_id, "resume": False, "state": state.model_dump(mode="json")}, config=config)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            await self.broker.publish(thread_id, {"type": "thread_failed", "thread_id": thread_id, "payload": {"error": str(exc)}})
            state = None
            try:
                state = await self.store.get_thread(thread_id)
            except Exception:
                state = None
            await self.audit.record(
                AuditEvent(
                    thread_id=thread_id,
                    step_id=state.current_step_id if state else None,
                    action_type="workflow",
                    event_type="THREAD_FAILED",
                    status="error",
                    error_code=type(exc).__name__,
                    payload_summary={"error": str(exc), "step_id": state.current_step_id if state else None},
                )
            )
            raise

    async def resume_thread(self, thread_id: str, resume_value: dict[str, Any]) -> None:
        graph = await self._ensure_graph()
        config = {"configurable": {"thread_id": thread_id}}
        await graph.ainvoke(Command(resume=resume_value), config=config)

    async def get_state_history(self, thread_id: str) -> list[ThreadHistoryEntry]:
        graph = await self._ensure_graph()
        config = {"configurable": {"thread_id": thread_id}}
        history: list[ThreadHistoryEntry] = []
        async for snapshot in graph.aget_state_history(config):
            history.append(
                ThreadHistoryEntry(
                    checkpoint_id=snapshot.config.get("configurable", {}).get("checkpoint_id"),
                    next_nodes=list(snapshot.next) if snapshot.next else [],
                    metadata=snapshot.metadata or {},
                    values=snapshot.values or {},
                )
            )
        return history

    def _build_graph(self):
        graph = StateGraph(dict)
        graph.add_node("intake_message", self.intake_message)
        graph.add_node("requirement_gap_check", self.requirement_gap_check)
        graph.add_node("series_guided_question", self.series_guided_question)
        graph.add_node("clarify_question", self.clarify_question)
        graph.add_node("confirm_requirements", self.confirm_requirements)
        graph.add_node("decision_update", self.decision_update)
        graph.add_node("source_parse", self.source_parse)
        graph.add_node("generate_step_artifact", self.generate_step_artifact)
        graph.add_node("critique_score", self.critique_score)
        graph.add_node("auto_improve", self.auto_improve)
        graph.add_node("human_review_interrupt", self.human_review_interrupt)
        graph.add_node("approved_feedback_merge", self.approved_feedback_merge)
        graph.add_node("revise_step_artifact", self.revise_step_artifact)
        graph.add_node("completion_gate", self.completion_gate)
        graph.add_node("apply_manual_feedback", self.apply_manual_feedback)

        graph.add_edge(START, "intake_message")
        graph.add_edge("intake_message", "requirement_gap_check")
        graph.add_conditional_edges(
            "requirement_gap_check",
            self.route_after_gap_check,
            {
                "series_guided_question": "series_guided_question",
                "clarify_question": "clarify_question",
                "confirm_requirements": "confirm_requirements",
                "decision_update": "decision_update",
                "apply_manual_feedback": "apply_manual_feedback",
            },
        )
        graph.add_edge("series_guided_question", END)
        graph.add_edge("clarify_question", END)
        graph.add_edge("confirm_requirements", END)
        graph.add_edge("decision_update", "source_parse")
        graph.add_edge("source_parse", "generate_step_artifact")
        graph.add_edge("generate_step_artifact", "critique_score")
        graph.add_conditional_edges(
            "critique_score",
            self.route_after_critique_score,
            {
                "auto_improve": "auto_improve",
                "human_review_interrupt": "human_review_interrupt",
            },
        )
        graph.add_edge("auto_improve", "critique_score")
        graph.add_edge("human_review_interrupt", "approved_feedback_merge")
        graph.add_edge("approved_feedback_merge", "revise_step_artifact")
        graph.add_conditional_edges(
            "revise_step_artifact",
            self.route_after_revise_step_artifact,
            {"critique_score": "critique_score", "completion_gate": "completion_gate"},
        )
        graph.add_edge("apply_manual_feedback", "critique_score")
        graph.add_conditional_edges(
            "completion_gate",
            self.route_after_completion_gate,
            {"review_pending": END, "completed": END},
        )
        return graph

    async def _load_state(self, raw_state: dict[str, Any]) -> ThreadState:
        if raw_state.get("state"):
            return ThreadState.model_validate(raw_state["state"])
        return await self.store.get_thread(raw_state["thread_id"])

    async def _save_state(
        self,
        state: ThreadState,
        node_name: str,
        event_type: str,
        payload_summary: dict[str, Any],
        *,
        model_config: LLMProviderConfig | None = None,
        prompt_id: str | None = None,
    ) -> dict[str, Any]:
        event_payload = dict(payload_summary)
        if prompt_id is not None:
            event_payload["prompt_id"] = prompt_id
        if model_config is not None:
            event_payload["profile_name"] = next(
                (name for name in ("chat", "clarify", "extract", "generate", "review", "improve") if self.deepseek.get_profile(name) == model_config),
                None,
            )
        await self.store.save_thread(state)
        await self.audit.record(
            AuditEvent(
                thread_id=state.thread_id,
                user_id=state.user_id,
                node_name=node_name,
                step_id=state.current_step_id,
                action_type=node_name,
                prompt_id=prompt_id,
                review_batch_id=event_payload.get("review_batch_id"),
                profile_name=event_payload.get("profile_name"),
                event_type=event_type,
                artifact_version=state.draft_artifact.version if state.draft_artifact else None,
                model_provider=model_config.provider if model_config else None,
                model_name=model_config.model if model_config else None,
                payload_summary=event_payload,
            )
        )
        await self.broker.publish(
            state.thread_id,
            {"type": "node_update", "thread_id": state.thread_id, "payload": {"node_name": node_name, "status": state.status, **event_payload}},
        )
        return {"thread_id": state.thread_id, "state": state.model_dump(mode="json"), **event_payload}

    def _current_step(self, state: ThreadState) -> StepBlueprint:
        return get_step_blueprint(state.current_step_id)

    def _current_step_state(self, state: ThreadState):
        return next((step for step in state.workflow_steps if step.step_id == state.current_step_id), None)

    def _current_step_artifact(self, state: ThreadState) -> StepArtifactRecord | None:
        return next((item for item in state.step_artifacts if item.step_id == state.current_step_id), None)

    def _start_generation_session(self, state: ThreadState, *, step: StepBlueprint, kind: GenerationRunKind, source_version: int | None = None, revision_goal: str | None = None) -> GenerationSessionState:
        session = GenerationSessionState(step_id=step.step_id, kind=kind, source_version=source_version, revision_goal=revision_goal)
        state.runtime.generation_session = session
        return session

    def _session(self, state: ThreadState) -> GenerationSessionState:
        if state.runtime.generation_session is None:
            state.runtime.generation_session = GenerationSessionState(step_id=state.current_step_id)
        return state.runtime.generation_session

    def _start_run(
        self,
        state: ThreadState,
        *,
        kind: GenerationRunKind,
        instruction: str | None = None,
        source_version: int | None = None,
        profile_name: str,
        prompt_id: str,
        action_type: str,
    ) -> GenerationRun:
        model_config = self.deepseek.get_profile(profile_name)
        run = GenerationRun(
            kind=kind,
            instruction=instruction,
            source_version=source_version,
            profile_name=profile_name,
            model_provider=model_config.provider,
            model_name=model_config.model,
            prompt_id=prompt_id,
            metadata={"step_id": state.current_step_id, "action_type": action_type},
        )
        state.generation_runs.append(run)
        self._session(state).active_generation_run_id = run.run_id
        return run

    def _complete_run(self, state: ThreadState, *, target_version: int | None = None, preview: str | None = None) -> None:
        run_id = self._session(state).active_generation_run_id
        if not run_id:
            return
        for run in reversed(state.generation_runs):
            if run.run_id == run_id:
                run.status = GenerationRunStatus.COMPLETED
                run.target_version = target_version
                run.output_preview = preview[:200] if preview else None
                run.completed_at = datetime.now(UTC)
                break

    def _extract_banned_terms(self, state: ThreadState) -> list[str]:
        terms: list[str] = []
        for item in state.conversation_constraints:
            if not item.active or item.kind != ConstraintKind.BAN:
                continue
            normalized = item.instruction
            for prefix in ["不要再沿用", "不要再用", "不要使用", "不要用", "不要", "别用", "不能用", "禁止", "避免", "排除"]:
                normalized = normalized.replace(prefix, "")
            for suffix in ["这个", "这种", "案例", "场景", "表达", "说法", "内容", "风格"]:
                normalized = normalized.replace(suffix, "")
            normalized = normalized.strip(" ：:，,。.;；")
            if normalized:
                terms.append(normalized)
        return list(dict.fromkeys(terms))

    async def _enforce_markdown_constraints(self, state: ThreadState, markdown: str, revision_goal: str) -> str:
        banned_terms = self._extract_banned_terms(state)
        violations = [term for term in banned_terms if term and term in markdown]
        if not violations:
            return markdown
        instruction = "当前稿件仍然出现被禁止内容：" + "、".join(violations) + "。必须完全移除这些内容，并替换成不同案例或表达，不能保留原词。"
        context_layers = await self._build_prompt_context_layers(state, self._current_step(state))
        return await self.deepseek.improve_markdown(
            prompt_id=self._current_step(state).improve_prompt_id or "improve.step_artifact",
            markdown=markdown,
            approved_changes=[instruction],
            structured_inputs=self._structured_inputs_text(context_layers.structured_input),
            confirmed_artifacts=self._confirmed_artifacts_text(context_layers.confirmed_artifacts),
            source_summary=context_layers.upload_summary,
            source_version=state.draft_artifact.version if state.draft_artifact else None,
            revision_goal=revision_goal,
            step_label=self._current_step(state).label,
            step_scope=self._step_scope(self._current_step(state)),
        )

    async def _timeline(self, thread_id: str, event_type: str, title: str, detail: str | None = None, payload: dict[str, Any] | None = None) -> None:
        await self.store.append_timeline_event(TimelineEvent(thread_id=thread_id, event_type=event_type, title=title, detail=detail, payload=payload or {}))

    def _build_structured_input(self, state: ThreadState, step: StepBlueprint) -> StepStructuredInput:
        slots: list[StepStructuredSlot] = []
        for slot_id in [*step.required_slots, *step.optional_slots]:
            slot = state.requirement_slots.get(slot_id)
            if slot and slot.value:
                slots.append(
                    StepStructuredSlot(
                        slot_id=slot.slot_id,
                        label=slot.label,
                        value=slot.value,
                        required=slot_id in step.required_slots,
                        confirmed=slot.confirmed,
                        source=slot.source,
                    )
                )
        return StepStructuredInput(step_id=step.step_id, step_label=step.label, slots=slots)

    def _structured_inputs_text(self, structured_input: StepStructuredInput) -> str:
        if not structured_input.slots:
            return "暂无当前步骤结构化输入"
        lines = []
        for slot in structured_input.slots:
            prefix = "必填" if slot.required else "可选"
            lines.append(f"- {prefix} | {slot.label}: {slot.value}")
        return "\n".join(lines)

    def _serialize_requirement_defs(self, step: StepBlueprint) -> list[dict[str, str]]:
        defs = []
        for slot_id in [*step.required_slots, *step.optional_slots]:
            slot = SLOT_DEFINITIONS[slot_id]
            defs.append({"slot_id": slot.slot_id, "label": slot.label, "prompt_hint": slot.prompt_hint, "patterns": list(slot.patterns)})
        return defs

    def _missing_required_slots(self, state: ThreadState, step: StepBlueprint) -> list[dict[str, str]]:
        missing: list[dict[str, str]] = []
        for slot_id in step.required_slots:
            slot = state.requirement_slots.get(slot_id)
            if not slot or not slot.value:
                slot_def = SLOT_DEFINITIONS[slot_id]
                missing.append({"slot_id": slot_id, "label": slot_def.label, "prompt_hint": slot_def.prompt_hint})
        return missing

    async def _confirmed_prior_step_artifacts(self, state: ThreadState, step: StepBlueprint) -> list[ConfirmedArtifactContext]:
        artifacts: list[ConfirmedArtifactContext] = []
        for prerequisite in step.prerequisite_step_ids:
            record = next((item for item in state.step_artifacts if item.step_id == prerequisite), None)
            if record is None or record.confirmed_version is None:
                continue
            try:
                detail = await self.store.get_artifact_version(state.thread_id, record.confirmed_version)
            except KeyError:
                continue
            artifacts.append(
                ConfirmedArtifactContext(
                    step_id=prerequisite,
                    step_label=record.label,
                    version=record.confirmed_version,
                    markdown=detail.markdown,
                )
            )
        return artifacts

    def _confirmed_artifacts_text(self, artifacts: list[ConfirmedArtifactContext]) -> str:
        if not artifacts:
            return "暂无已确认前序产物"
        return "\n\n".join(f"## {item.step_label} (v{item.version})\n{item.markdown[:2000]}" for item in artifacts)

    def _upload_summary(self, state: ThreadState) -> str:
        source_summary = []
        for document in state.source_manifest:
            if document.extract_status != "parsed":
                continue
            if document.metadata.get("category") != "context":
                continue
            preview = " ".join(chunk.text for chunk in document.text_chunks[:2])
            source_summary.append(f"{document.filename}: {preview[:200]}")
        return "\n".join(source_summary) or "无上传资料"

    def _step_scope(self, step: StepBlueprint) -> str:
        prior = "、".join(step.prerequisite_step_ids) if step.prerequisite_step_ids else "无前序步骤"
        forbidden = "、".join(step.forbidden_topics) if step.forbidden_topics else "无显式禁区"
        return f"当前步骤是“{step.label}”。允许依赖当前步骤结构化输入、已确认的前序步骤产物和上传资料摘要；前序范围：{prior}；禁止提前展开：{forbidden}。"

    def _output_contract(self, step: StepBlueprint, purpose: str) -> str:
        mapping = {
            "clarify": f"只追问“{step.label}”当前缺失的一个字段。",
            "generate": f"只生成“{step.label}”正式草稿，不提前产出其他步骤内容。",
            "review": f"只评审“{step.label}”当前版本，不把未来步骤当成扣分项。",
            "improve": f"只修订“{step.label}”当前版本，不改写前序已确认产物。",
        }
        return mapping[purpose]

    async def _build_prompt_context_layers(self, state: ThreadState, step: StepBlueprint) -> PromptContextLayers:
        return PromptContextLayers(
            raw_session_messages=list(state.messages),
            structured_input=self._build_structured_input(state, step),
            confirmed_artifacts=await self._confirmed_prior_step_artifacts(state, step),
            upload_summary=self._upload_summary(state),
        )

    async def _persist_current_step_artifact(self, state: ThreadState) -> SavedArtifactRecord | None:
        if state.draft_artifact is None:
            return None
        step_state = self._current_step_state(state)
        if step_state is None:
            return None
        artifact_dir = self.settings.storage_dir / state.thread_id / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        filename = get_step_blueprint(step_state.step_id).artifact_filename
        path = artifact_dir / filename
        path.write_text(state.draft_artifact.markdown, encoding="utf-8")

        existing = next((item for item in state.saved_artifacts if item.step_id == step_state.step_id and item.kind == "generated"), None)
        if existing:
            existing.path = str(path)
            existing.filename = filename
            existing.label = step_state.label
            existing.version += 1
            existing.updated_at = datetime.now(UTC)
            step_state.artifact_id = existing.artifact_id
            return existing

        record = SavedArtifactRecord(step_id=step_state.step_id, label=step_state.label, filename=filename, path=str(path), kind="generated")
        state.saved_artifacts.append(record)
        step_state.artifact_id = record.artifact_id
        return record

    def _sync_current_step_artifact_state(self, state: ThreadState, *, review_batch_id: str | None = None) -> None:
        if state.draft_artifact is None:
            return
        record = self._current_step_artifact(state)
        step_state = self._current_step_state(state)
        if record is None and step_state is not None:
            record = StepArtifactRecord(step_id=step_state.step_id, label=step_state.label)
            state.step_artifacts.append(record)
        if record is None:
            return
        record.status = StepArtifactStatus.GENERATED
        record.current_artifact_id = state.draft_artifact.artifact_id
        record.current_version = state.draft_artifact.version
        if review_batch_id is not None:
            record.latest_review_batch_id = review_batch_id
        record.updated_at = datetime.now(UTC)

    def _review_threshold(self, step: StepBlueprint) -> float:
        if step.step_id == "series_framework":
            return 80.0
        return self.settings.default_review_threshold

    def _is_series_step(self, state: ThreadState, step: StepBlueprint | None = None) -> bool:
        current = step or self._current_step(state)
        return state.course_mode == CourseMode.SERIES and current.step_id == "series_framework"

    def _sync_series_answer_slot(self, state: ThreadState, slot_id: str, value: str) -> None:
        slot = state.requirement_slots.get(slot_id) or get_slot_definition(slot_id)
        slot.value = value
        slot.confidence = 1.0
        slot.confirmed = True
        state.requirement_slots[slot_id] = slot

    def _infer_series_topic(self, user_input: str) -> str:
        text = user_input.strip()
        match = re.search(r"做(?:一套|一门)?(.+?)(?:系列课|课程)", text)
        if match:
            topic = match.group(1).strip("：:，,。 ")
            if topic:
                return topic
        for marker in ("帮助", "面向", "让", "用于"):
            if marker in text:
                return text.split(marker, 1)[0].strip("：:，,。 ")
        return text[:40] if text else "待补充主题"

    def _hydrate_series_slots_from_framework(self, state: ThreadState, markdown_text: str) -> None:
        framework = parse_framework_markdown(markdown_text)
        lesson_count = len(framework.lessons)
        course_size = f"标准系统课：{lesson_count} 课" if lesson_count else "待补充"
        course_type = "方法认知型"
        if any(token in framework.course_name + framework.core_problem for token in ("转型", "岗位", "职业")):
            course_type = "职业转型型"
        elif any(token in framework.course_name + framework.core_problem for token in ("实战", "案例", "项目", "工作流", "搭建")):
            course_type = "技能实操型"
        learning_goal = framework.learner_expected_state if framework.learner_expected_state != "待补充" else framework.core_problem
        self._sync_series_answer_slot(state, "topic", framework.course_name)
        self._sync_series_answer_slot(state, "course_type", course_type)
        self._sync_series_answer_slot(state, "target_user", framework.target_user)
        self._sync_series_answer_slot(state, "learning_goal", learning_goal)
        self._sync_series_answer_slot(state, "mindset_shift", framework.mindset_shift)
        self._sync_series_answer_slot(state, "course_size", course_size)
        self._sync_series_answer_slot(state, "application", framework.application_scenario)

    async def _handle_series_guided_requirement_gap(self, state: ThreadState, step: StepBlueprint, latest_user_message: str) -> dict[str, Any]:
        guided = state.runtime.series_guided
        raw_text = latest_user_message.strip()

        if guided.ready_to_generate:
            state.runtime.clarification.missing_requirements = self._missing_required_slots(state, step)
            state.runtime.clarification.next_requirement_to_clarify = None
            state.runtime.clarification.slot_summary = self._structured_inputs_text(self._build_structured_input(state, step))
            state.runtime.clarification.latest_user_message = latest_user_message
            state.runtime.clarification.is_confirmation_reply = True
            return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [], "mode": "series_ready_to_generate"})

        if guided.completed:
            missing = self._missing_required_slots(state, step)
            state.runtime.clarification.missing_requirements = missing
            state.runtime.clarification.next_requirement_to_clarify = missing[0]["slot_id"] if missing else None
            state.runtime.clarification.slot_summary = self._structured_inputs_text(self._build_structured_input(state, step))
            state.runtime.clarification.latest_user_message = latest_user_message
            state.runtime.clarification.is_confirmation_reply = bool(raw_text and any(re.search(pattern, raw_text) for pattern in CONFIRMATION_PATTERNS))
            return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [item["slot_id"] for item in missing], "mode": "series_confirm"})

        if guided.awaiting_entry_mode:
            choice = raw_text.upper()
            if choice == "A":
                guided.entry_mode = "guided"
                guided.awaiting_entry_mode = False
                guided.awaiting_initial_idea = True
                guided.current_question_prompt = "请输入你的制课想法，我会先帮你把主题收束，再进入系列课结构化问答。"
            elif choice == "B":
                guided.entry_mode = "framework"
                guided.awaiting_entry_mode = False
                guided.awaiting_framework_input = True
                guided.current_question_prompt = "请粘贴你的课程框架，或直接上传框架文件；我会按系列课标准直接评分并给出优化建议。"
            else:
                guided.current_question_prompt = SERIES_STARTER_PROMPT + "\n\n请直接回复 A 或 B。"
            return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [], "mode": "series_entry"})

        if guided.awaiting_initial_idea:
            guided.initial_user_input = raw_text
            guided.awaiting_initial_idea = False
            guided.next_question_index = 0
            self._sync_series_answer_slot(state, "topic", self._infer_series_topic(raw_text))
            question = QUESTION_FLOW[guided.next_question_index]
            guided.current_question_id = question.step.value
            guided.current_question_prompt = render_question_prompt(question, 1, len(QUESTION_FLOW))
            return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [], "mode": "series_initial_idea"})

        if guided.awaiting_framework_input:
            if not raw_text:
                guided.current_question_prompt = "请先粘贴你的课程框架，或上传框架文件后我再继续。"
            else:
                guided.awaiting_framework_input = False
                guided.using_existing_framework = True
                guided.imported_framework_markdown = latest_user_message.strip()
                guided.completed = True
                guided.ready_to_generate = True
                self._hydrate_series_slots_from_framework(state, guided.imported_framework_markdown)
            return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [], "mode": "series_framework_input"})

        question = get_question_by_step(guided.current_question_id or "")
        if question is None and guided.next_question_index < len(QUESTION_FLOW):
            question = QUESTION_FLOW[guided.next_question_index]
            guided.current_question_id = question.step.value
        if question is None:
            guided.completed = True
            state.runtime.clarification.slot_summary = self._structured_inputs_text(self._build_structured_input(state, step))
            return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [], "mode": "series_fallback_complete"})

        try:
            answer = parse_user_answer(question, latest_user_message)
        except ValueError as exc:
            guided.current_question_prompt = f"{exc}\n\n{render_question_prompt(question, guided.next_question_index + 1, len(QUESTION_FLOW))}"
            return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [], "mode": "series_retry_question"})

        guided.answers[answer.step.value] = answer
        self._sync_series_answer_slot(state, answer.step.value, answer.final_answer)
        guided.next_question_index += 1
        if guided.next_question_index < len(QUESTION_FLOW):
            next_question = QUESTION_FLOW[guided.next_question_index]
            guided.current_question_id = next_question.step.value
            guided.current_question_prompt = render_question_prompt(next_question, guided.next_question_index + 1, len(QUESTION_FLOW))
        else:
            guided.current_question_id = None
            guided.current_question_prompt = None
            guided.completed = True
            guided.awaiting_confirmation = True
            state.runtime.clarification.missing_requirements = []
            state.runtime.clarification.next_requirement_to_clarify = None
            state.runtime.clarification.slot_summary = self._structured_inputs_text(self._build_structured_input(state, step))
            state.runtime.clarification.latest_user_message = latest_user_message
            state.runtime.clarification.is_confirmation_reply = False
        return await self._save_state(state, "requirement_gap_check", "GRAPH_NODE_COMPLETED", {"step_id": step.step_id, "missing_requirements": [], "mode": "series_questionnaire"})

    async def intake_message(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        return await self._save_state(state, "intake_message", "GRAPH_NODE_ENTERED", {"message_count": len(state.messages), "step_id": state.current_step_id})

    async def requirement_gap_check(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        latest_user_message = next((m.content for m in reversed(state.messages) if m.role == MessageRole.USER), "")
        if state.runtime.pending_manual_revision_request and state.draft_artifact is not None and state.draft_artifact.step_id == step.step_id:
            state.runtime.clarification.missing_requirements = []
            state.runtime.clarification.next_requirement_to_clarify = None
            state.runtime.clarification.slot_summary = self._structured_inputs_text(self._build_structured_input(state, step))
            state.runtime.clarification.latest_user_message = latest_user_message
            state.runtime.clarification.is_confirmation_reply = False
            return await self._save_state(
                state,
                "requirement_gap_check",
                "GRAPH_NODE_COMPLETED",
                {"step_id": step.step_id, "missing_requirements": [], "mode": "manual_feedback"},
                model_config=self.deepseek.get_profile("extract"),
                prompt_id="extract.requirements",
            )

        if self._is_series_step(state, step):
            return await self._handle_series_guided_requirement_gap(state, step, latest_user_message)

        current_values = {
            slot_id: slot.value
            for slot_id, slot in state.requirement_slots.items()
            if slot.value and slot_id in {*step.required_slots, *step.optional_slots}
        }
        requirement_defs = self._serialize_requirement_defs(step)
        extracted = await self.deepseek.extract_requirements(
            latest_user_message=latest_user_message,
            known_requirements=current_values,
            requirement_defs=requirement_defs,
        )
        for definition in requirement_defs:
            slot = state.requirement_slots.get(definition["slot_id"]) or get_slot_definition(definition["slot_id"])
            if not slot.value:
                llm_value = extracted.get(definition["slot_id"])
                if llm_value:
                    slot.value = llm_value.strip()
                    slot.confidence = 0.92
                for pattern in definition["patterns"]:
                    if slot.value:
                        break
                    match = re.search(pattern, latest_user_message)
                    if match:
                        slot.value = match.group(1).strip() if match.groups() else match.group(0).strip()
                        slot.confidence = 0.85
                        break
            if slot.value:
                slot.confirmed = True
            state.requirement_slots[definition["slot_id"]] = slot

        missing = self._missing_required_slots(state, step)
        state.runtime.clarification.missing_requirements = missing
        state.runtime.clarification.next_requirement_to_clarify = missing[0]["slot_id"] if missing else None
        state.runtime.clarification.slot_summary = self._structured_inputs_text(self._build_structured_input(state, step))
        state.runtime.clarification.latest_user_message = latest_user_message
        state.runtime.clarification.is_confirmation_reply = bool(latest_user_message and any(re.search(pattern, latest_user_message.strip()) for pattern in CONFIRMATION_PATTERNS))
        return await self._save_state(
            state,
            "requirement_gap_check",
            "GRAPH_NODE_COMPLETED",
            {"step_id": step.step_id, "missing_requirements": [item["slot_id"] for item in missing]},
            model_config=self.deepseek.get_profile("extract"),
            prompt_id="extract.requirements",
        )

    def route_after_gap_check(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        if state.runtime.pending_manual_revision_request and state.draft_artifact is not None:
            return "apply_manual_feedback"
        if state.course_mode == CourseMode.SERIES and state.runtime.series_guided.current_question_prompt and not state.runtime.series_guided.completed:
            return "series_guided_question"
        if state.course_mode == CourseMode.SERIES and state.runtime.series_guided.ready_to_generate:
            return "decision_update"
        if state.runtime.clarification.missing_requirements:
            return "clarify_question"
        if state.runtime.clarification.is_confirmation_reply:
            return "decision_update"
        return "confirm_requirements"

    async def series_guided_question(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        prompt = state.runtime.series_guided.current_question_prompt
        if not prompt:
            return await self._save_state(state, "series_guided_question", "GRAPH_NODE_SKIPPED", {"reason": "no_series_prompt"})
        state.messages.append(MessageRecord(role=MessageRole.ASSISTANT, content=prompt))
        state.status = ThreadStatus.COLLECTING
        state.runtime.series_guided.current_question_prompt = None
        await self.broker.publish(state.thread_id, {"type": "assistant_message", "thread_id": state.thread_id, "payload": {"content": prompt}})
        await self._timeline(state.thread_id, "series_guided_prompt", "系列课结构化问题已发出", detail=prompt[:160], payload={"step_id": state.current_step_id})
        return await self._save_state(state, "series_guided_question", "SERIES_GUIDED_PROMPTED", {"step_id": state.current_step_id, "prompt": prompt[:160]})

    async def clarify_question(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        context_layers = await self._build_prompt_context_layers(state, step)
        missing_slot_id = state.runtime.clarification.next_requirement_to_clarify
        missing_requirement = next((item for item in state.runtime.clarification.missing_requirements if item["slot_id"] == missing_slot_id), None)
        if missing_requirement is None:
            return await self._save_state(state, "clarify_question", "GRAPH_NODE_SKIPPED", {"reason": "no_missing_requirement"})
        question = ""
        await self.broker.publish(state.thread_id, {"type": "clarification_started", "thread_id": state.thread_id, "payload": {"slot_id": missing_slot_id, "step_id": step.step_id}})
        async for chunk in self.deepseek.stream_clarification(
            {
                "prompt_id": step.clarify_prompt_id or f"clarify.{step.step_id}",
                "step_label": step.label,
                "step_scope": self._step_scope(step),
                "allowed_input_layers": "当前步骤结构化输入、上传资料摘要",
                "forbidden_input_layers": "原始聊天消息、未来步骤内容、未确认产物",
                "output_contract": self._output_contract(step, "clarify"),
                "allowed_scope": "、".join([SLOT_DEFINITIONS[item].label for item in [*step.required_slots, *step.optional_slots]]) or "无",
                "forbidden_scope": "、".join(step.forbidden_topics) or "无",
                "structured_inputs": self._structured_inputs_text(context_layers.structured_input),
                "missing_requirement": missing_requirement,
            }
        ):
            question += chunk
            await self.broker.publish(state.thread_id, {"type": "assistant_token", "thread_id": state.thread_id, "payload": {"content": chunk}})
        state.messages.append(MessageRecord(role=MessageRole.ASSISTANT, content=question))
        state.status = ThreadStatus.COLLECTING
        await self.broker.publish(state.thread_id, {"type": "assistant_stream_end", "thread_id": state.thread_id, "payload": {"content": question}})
        await self.broker.publish(state.thread_id, {"type": "clarification_completed", "thread_id": state.thread_id, "payload": {"content": question}})
        await self._timeline(state.thread_id, "clarification_completed", f"{step.label}缺失项追问已发出", detail=question[:160], payload={"step_id": step.step_id})
        return await self._save_state(
            state,
            "clarify_question",
            "CLARIFICATION_REQUESTED",
            {"question": question[:160], "step_id": step.step_id},
            model_config=self.deepseek.get_profile("clarify"),
            prompt_id=step.clarify_prompt_id or f"clarify.{step.step_id}",
        )

    async def confirm_requirements(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        summary = state.runtime.clarification.slot_summary
        confirmation_message = (
            f"当前在“{step.label}”这一步，我先把会影响这一步生成的关键信息整理一下：\n\n"
            f"{summary}\n\n"
            f"这一步只会生成“{step.label}”相关内容，不会提前展开 {('、'.join(step.forbidden_topics) or '后续步骤')}。"
            " 如果这些信息没问题，你回复“开始生成”即可。"
        )
        state.messages.append(MessageRecord(role=MessageRole.ASSISTANT, content=confirmation_message))
        state.status = ThreadStatus.COLLECTING
        await self.broker.publish(state.thread_id, {"type": "assistant_message", "thread_id": state.thread_id, "payload": {"content": confirmation_message}})
        return await self._save_state(state, "confirm_requirements", "REQUIREMENTS_READY_FOR_CONFIRMATION", {"step_id": step.step_id, "summary": summary[:200]})

    async def decision_update(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        for slot_id in [*step.required_slots, *step.optional_slots]:
            slot = state.requirement_slots.get(slot_id)
            if slot and slot.value and slot.confirmed:
                if not any(item.topic == slot.slot_id and item.value == slot.value for item in state.decision_ledger):
                    state.decision_ledger.append(DecisionItem(topic=slot.slot_id, value=slot.value, reason=f"从{step.label}对话中确认"))
                if slot.slot_id == "constraints":
                    normalized = re.sub(r"\s+", "", slot.value).strip().lower()
                    if normalized and not any(item.normalized_instruction == normalized for item in state.conversation_constraints):
                        state.conversation_constraints.append(ConversationConstraint(kind=ConstraintKind.REQUIRE, instruction=slot.value, normalized_instruction=normalized))
        state.decision_summary = "\n".join(f"{item.topic}: {item.value}" for item in state.decision_ledger)
        state.requirements_confirmed = True
        if self._is_series_step(state, step):
            state.runtime.series_guided.ready_to_generate = False
        await self._timeline(state.thread_id, "requirements_confirmed", f"{step.label}需求已确认", payload={"decision_count": len(state.decision_ledger), "step_id": step.step_id})
        return await self._save_state(state, "decision_update", "DECISION_CONFIRMED", {"decision_count": len(state.decision_ledger), "step_id": step.step_id})

    async def source_parse(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        session = self._session(state)
        session.source_summary = self._upload_summary(state)
        return await self._save_state(state, "source_parse", "GRAPH_NODE_COMPLETED", {"source_count": len(state.source_manifest), "step_id": state.current_step_id})

    async def generate_step_artifact(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        if self._is_series_step(state, step) and state.runtime.series_guided.using_existing_framework and state.runtime.series_guided.imported_framework_markdown:
            markdown = state.runtime.series_guided.imported_framework_markdown.strip()
            next_version = max([item.version for item in await self.store.list_versions(state.thread_id)], default=0) + 1
            artifact = DraftArtifact(step_id=step.step_id, version=next_version, markdown=markdown, summary=f"{step.label}已导入。")
            state.status = ThreadStatus.GENERATING
            state.draft_artifact = artifact
            state.version_chain.append(VersionRecord(version=artifact.version, artifact_id=artifact.artifact_id, step_id=step.step_id))
            await self.store.upsert_artifact_version(state.thread_id, artifact)
            await self._persist_current_step_artifact(state)
            self._sync_current_step_artifact_state(state)
            await self.broker.publish(state.thread_id, {"type": "generation_completed", "thread_id": state.thread_id, "payload": {"version": artifact.version, "step_id": step.step_id, "source": "framework_import"}})
            await self._timeline(state.thread_id, "generation_completed", f"{step.label}已导入", payload={"version": artifact.version, "step_id": step.step_id, "source": "framework_import"})
            return await self._save_state(state, "generate_step_artifact", "DRAFT_GENERATED", {"artifact_version": artifact.version, "step_id": step.step_id, "source": "framework_import"})

        context_layers = await self._build_prompt_context_layers(state, step)
        session = self._start_generation_session(state, step=step, kind=GenerationRunKind.GENERATION, revision_goal=step.generation_goal)
        state.status = ThreadStatus.GENERATING
        session.generated_markdown = ""
        state.draft_artifact = DraftArtifact(step_id=step.step_id, version=0, markdown="", summary=f"{step.label}生成中...")
        run = self._start_run(
            state,
            kind=GenerationRunKind.GENERATION,
            instruction=step.generation_goal,
            profile_name="generate",
            prompt_id=step.generate_prompt_id or "generate.legacy_full_draft",
            action_type="generate",
        )
        await self.broker.publish(state.thread_id, {"type": "generation_started", "thread_id": state.thread_id, "payload": {"run_id": run.run_id, "step_id": step.step_id}})
        await self._timeline(state.thread_id, "generation_started", f"开始生成{step.label}", payload={"run_id": run.run_id, "step_id": step.step_id})

        async for chunk in self.deepseek.stream_step_markdown(
            {
                "prompt_id": step.generate_prompt_id,
                "step_label": step.label,
                "step_scope": self._step_scope(step),
                "allowed_input_layers": "当前步骤结构化输入、已确认前序产物、上传资料摘要",
                "forbidden_input_layers": "原始聊天消息、未来步骤内容、未确认产物、reasoning 内容",
                "output_contract": self._output_contract(step, "generate"),
                "generation_goal": step.generation_goal,
                "structured_inputs": self._structured_inputs_text(context_layers.structured_input),
                "confirmed_artifacts": self._confirmed_artifacts_text(context_layers.confirmed_artifacts),
                "source_summary": context_layers.upload_summary,
            }
        ):
            session.generated_markdown += chunk
            state.draft_artifact.markdown = session.generated_markdown
            await self.broker.publish(state.thread_id, {"type": "generation_chunk", "thread_id": state.thread_id, "payload": {"content": chunk, "step_id": step.step_id}})
        session.generated_markdown = await self._enforce_markdown_constraints(state, session.generated_markdown, step.generation_goal)
        next_version = max([item.version for item in await self.store.list_versions(state.thread_id)], default=0) + 1
        artifact = DraftArtifact(
            step_id=step.step_id,
            version=next_version,
            markdown=session.generated_markdown,
            summary=f"{step.label}已生成。",
            source_version=session.source_version,
            revision_goal=step.generation_goal,
            generation_run_id=session.active_generation_run_id,
        )
        state.draft_artifact = artifact
        state.version_chain.append(VersionRecord(version=artifact.version, artifact_id=artifact.artifact_id, step_id=step.step_id, source_version=artifact.source_version, revision_goal=artifact.revision_goal, generation_run_id=artifact.generation_run_id))
        await self.store.upsert_artifact_version(state.thread_id, artifact)
        await self._persist_current_step_artifact(state)
        self._sync_current_step_artifact_state(state)
        self._complete_run(state, target_version=artifact.version, preview=artifact.markdown)
        await self.broker.publish(state.thread_id, {"type": "artifact_updated", "thread_id": state.thread_id, "payload": artifact.model_dump(mode="json")})
        await self.broker.publish(state.thread_id, {"type": "generation_completed", "thread_id": state.thread_id, "payload": {"version": artifact.version, "step_id": step.step_id}})
        await self._timeline(state.thread_id, "generation_completed", f"{step.label}已生成", payload={"version": artifact.version, "step_id": step.step_id})
        return await self._save_state(
            state,
            "generate_step_artifact",
            "DRAFT_GENERATED",
            {"artifact_version": artifact.version, "step_id": step.step_id},
            model_config=self.deepseek.get_profile("generate"),
            prompt_id=step.generate_prompt_id,
        )

    async def critique_score(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        state.status = ThreadStatus.REVIEW_PENDING
        threshold = self._review_threshold(step)
        if self._is_series_step(state, step):
            report = await score_series_framework_markdown(state.draft_artifact.markdown, self.deepseek)
            result = {
                "total_score": report.total_score,
                "criteria": [
                    {
                        "criterion_id": item.criterion_id,
                        "name": item.name,
                        "weight": item.weight,
                        "score": item.score,
                        "max_score": item.max_score,
                        "reason": item.reason,
                    }
                    for item in report.criteria
                ],
                "suggestions": [
                    {
                        "criterion_id": item.criterion_id,
                        "problem": item.problem,
                        "suggestion": item.suggestion,
                        "evidence_span": item.evidence_span,
                        "severity": item.severity,
                    }
                    for item in report.suggestions
                ],
            }
        else:
            result = await self.deepseek.review_markdown(
                prompt_id=step.review_prompt_id or "review.step_artifact",
                markdown=state.draft_artifact.markdown,
                rubric=RUBRIC,
                threshold=threshold,
                step_label=step.label,
                step_scope=self._step_scope(step),
                allowed_input_layers="当前步骤结构化输入、已确认前序产物、上传资料摘要、当前步骤产物",
                forbidden_input_layers="未来步骤内容、未确认产物、reasoning 内容、原始聊天消息",
                forbidden_topics="、".join(step.forbidden_topics) or "无",
            )
        batch = ReviewBatch(
            step_id=step.step_id,
            draft_version=state.draft_artifact.version,
            total_score=float(result["total_score"]),
            criteria=[ReviewCriterionResult.model_validate(item) for item in result["criteria"]],
            suggestions=[ReviewSuggestion.model_validate(item) for item in result["suggestions"]],
            threshold=threshold,
        )
        state.review_batches.append(batch)
        self._session(state).review_batch_id = batch.review_batch_id
        self._sync_current_step_artifact_state(state, review_batch_id=batch.review_batch_id)
        await self.store.append_review_batch(state.thread_id, batch)
        await self.broker.publish(state.thread_id, {"type": "review_batch", "thread_id": state.thread_id, "payload": batch.model_dump(mode="json")})
        await self.broker.publish(state.thread_id, {"type": "review_ready", "thread_id": state.thread_id, "payload": batch.model_dump(mode="json")})
        await self._timeline(state.thread_id, "review_ready", f"{step.label}评审建议已生成", payload={"review_batch_id": batch.review_batch_id, "score": batch.total_score, "step_id": step.step_id})
        return await self._save_state(
            state,
            "critique_score",
            "REVIEW_BATCH_CREATED",
            {"review_batch_id": batch.review_batch_id, "score": batch.total_score, "step_id": step.step_id},
            model_config=self.deepseek.get_profile("review"),
            prompt_id=step.review_prompt_id or "review.step_artifact",
        )

    def route_after_critique_score(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        latest_review = state.review_batches[-1]
        loops = state.runtime.generation_session.auto_optimization_loops if state.runtime.generation_session else 0
        if latest_review.total_score < latest_review.threshold and loops < self.settings.max_auto_optimization_loops:
            return "auto_improve"
        return "human_review_interrupt"

    async def auto_improve(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        latest_review = state.review_batches[-1]
        context_layers = await self._build_prompt_context_layers(state, step)
        state.status = ThreadStatus.REVISING
        session = self._session(state)
        session.auto_optimization_loops += 1
        session.source_version = state.draft_artifact.version if state.draft_artifact else None
        session.revision_goal = f"根据自动评审建议补强{step.label}"
        self._start_run(
            state,
            kind=GenerationRunKind.REVISION,
            instruction=session.revision_goal,
            source_version=session.source_version,
            profile_name="improve",
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
            action_type="auto_improve",
        )
        session.generated_markdown = await self.deepseek.improve_markdown(
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
            markdown=state.draft_artifact.markdown,
            approved_changes=[suggestion.suggestion for suggestion in latest_review.suggestions],
            structured_inputs=self._structured_inputs_text(context_layers.structured_input),
            confirmed_artifacts=self._confirmed_artifacts_text(context_layers.confirmed_artifacts),
            source_summary=context_layers.upload_summary,
            source_version=session.source_version,
            revision_goal=session.revision_goal,
            step_label=step.label,
            step_scope=self._step_scope(step),
        )
        artifact = DraftArtifact(
            step_id=step.step_id,
            version=(state.draft_artifact.version + 1),
            markdown=session.generated_markdown,
            summary=f"{step.label}自动优化版本。",
            source_version=session.source_version,
            revision_goal=session.revision_goal,
            generation_run_id=session.active_generation_run_id,
        )
        state.draft_artifact = artifact
        state.version_chain.append(VersionRecord(version=artifact.version, artifact_id=artifact.artifact_id, step_id=step.step_id, source_version=artifact.source_version, revision_goal=artifact.revision_goal, generation_run_id=artifact.generation_run_id))
        await self.store.upsert_artifact_version(state.thread_id, artifact)
        await self._persist_current_step_artifact(state)
        self._sync_current_step_artifact_state(state)
        self._complete_run(state, target_version=artifact.version, preview=artifact.markdown)
        await self.broker.publish(state.thread_id, {"type": "revision_started", "thread_id": state.thread_id, "payload": {"loop": session.auto_optimization_loops, "step_id": step.step_id}})
        return await self._save_state(
            state,
            "auto_improve",
            "DRAFT_REVISED",
            {"mode": "auto", "loop": session.auto_optimization_loops, "step_id": step.step_id},
            model_config=self.deepseek.get_profile("improve"),
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
        )

    async def human_review_interrupt(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        batch = state.review_batches[-1]
        payload = InterruptPayload(review_batch_id=batch.review_batch_id, draft_version=batch.draft_version, total_score=batch.total_score, criteria=batch.criteria, suggestions=batch.suggestions)
        state.runtime.human_review.interrupt_payload = payload
        await self.store.save_thread(state)
        resume_value = interrupt(payload.model_dump(mode="json"))
        state.runtime.human_review.resume_payload = ResumePayload.model_validate(resume_value)
        return await self._save_state(state, "human_review_interrupt", "REVIEW_INTERRUPTED", {"review_batch_id": batch.review_batch_id, "step_id": step.step_id})

    async def approved_feedback_merge(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        payload = state.runtime.human_review.resume_payload
        state.approved_feedback = payload.review_actions if payload else []
        return await self._save_state(state, "approved_feedback_merge", "GRAPH_NODE_COMPLETED", {"approved_count": len(state.approved_feedback), "step_id": state.current_step_id})

    async def revise_step_artifact(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        context_layers = await self._build_prompt_context_layers(state, step)
        instructions = [action.edited_suggestion or f"根据人工确认意见补强{step.label}。" for action in state.approved_feedback if action.action != "reject"]
        if not instructions:
            return await self._save_state(state, "revise_step_artifact", "GRAPH_NODE_SKIPPED", {"reason": "no_approved_feedback", "step_id": step.step_id, "revised": False})
        session = self._start_generation_session(state, step=step, kind=GenerationRunKind.REVISION, source_version=state.draft_artifact.version if state.draft_artifact else None, revision_goal=f"根据人工审核意见补强{step.label}")
        self._start_run(
            state,
            kind=GenerationRunKind.REVISION,
            instruction=session.revision_goal,
            source_version=session.source_version,
            profile_name="improve",
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
            action_type="revise",
        )
        state.status = ThreadStatus.REVISING
        session.generated_markdown = await self.deepseek.improve_markdown(
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
            markdown=state.draft_artifact.markdown,
            approved_changes=instructions,
            structured_inputs=self._structured_inputs_text(context_layers.structured_input),
            confirmed_artifacts=self._confirmed_artifacts_text(context_layers.confirmed_artifacts),
            source_summary=context_layers.upload_summary,
            source_version=session.source_version,
            revision_goal=session.revision_goal,
            step_label=step.label,
            step_scope=self._step_scope(step),
        )
        artifact = DraftArtifact(
            step_id=step.step_id,
            version=(state.draft_artifact.version + 1),
            markdown=session.generated_markdown,
            summary=f"{step.label}人工修订版本。",
            source_version=session.source_version,
            revision_goal=session.revision_goal,
            generation_run_id=session.active_generation_run_id,
        )
        state.draft_artifact = artifact
        state.version_chain.append(VersionRecord(version=artifact.version, artifact_id=artifact.artifact_id, step_id=step.step_id, source_version=artifact.source_version, revision_goal=artifact.revision_goal, generation_run_id=artifact.generation_run_id))
        await self.store.upsert_artifact_version(state.thread_id, artifact)
        await self._persist_current_step_artifact(state)
        self._sync_current_step_artifact_state(state)
        self._complete_run(state, target_version=artifact.version, preview=artifact.markdown)
        await self.broker.publish(state.thread_id, {"type": "revision_started", "thread_id": state.thread_id, "payload": {"approved_count": len(instructions), "step_id": step.step_id}})
        return await self._save_state(
            state,
            "revise_step_artifact",
            "DRAFT_REVISED",
            {"step_id": step.step_id, "approved_count": len(instructions), "revised": True},
            model_config=self.deepseek.get_profile("improve"),
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
        )

    async def apply_manual_feedback(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        context_layers = await self._build_prompt_context_layers(state, step)
        instruction = state.runtime.pending_manual_revision_request or ""
        if not instruction or state.draft_artifact is None:
            return await self._save_state(state, "apply_manual_feedback", "GRAPH_NODE_SKIPPED", {"reason": "no_manual_revision_request", "step_id": step.step_id})
        session = self._start_generation_session(state, step=step, kind=GenerationRunKind.REVISION, source_version=state.draft_artifact.version, revision_goal=instruction)
        self._start_run(
            state,
            kind=GenerationRunKind.REVISION,
            instruction=instruction,
            source_version=state.draft_artifact.version,
            profile_name="improve",
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
            action_type="manual_improve",
        )
        state.status = ThreadStatus.REVISING
        session.generated_markdown = await self.deepseek.improve_markdown(
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
            markdown=state.draft_artifact.markdown,
            approved_changes=[instruction],
            structured_inputs=self._structured_inputs_text(context_layers.structured_input),
            confirmed_artifacts=self._confirmed_artifacts_text(context_layers.confirmed_artifacts),
            source_summary=context_layers.upload_summary,
            source_version=state.draft_artifact.version,
            revision_goal=instruction,
            step_label=step.label,
            step_scope=self._step_scope(step),
        )
        artifact = DraftArtifact(
            step_id=step.step_id,
            version=(state.draft_artifact.version + 1),
            markdown=session.generated_markdown,
            summary=f"{step.label}按用户补充意见修订。",
            source_version=state.draft_artifact.version,
            revision_goal=instruction,
            generation_run_id=session.active_generation_run_id,
        )
        state.draft_artifact = artifact
        state.version_chain.append(VersionRecord(version=artifact.version, artifact_id=artifact.artifact_id, step_id=step.step_id, source_version=artifact.source_version, revision_goal=artifact.revision_goal, generation_run_id=artifact.generation_run_id))
        await self.store.upsert_artifact_version(state.thread_id, artifact)
        await self._persist_current_step_artifact(state)
        self._complete_run(state, target_version=artifact.version, preview=artifact.markdown)
        state.runtime.pending_manual_revision_request = None
        return await self._save_state(
            state,
            "apply_manual_feedback",
            "USER_FEEDBACK_APPLIED",
            {"instruction": instruction[:160], "step_id": step.step_id},
            model_config=self.deepseek.get_profile("improve"),
            prompt_id=step.improve_prompt_id or "improve.step_artifact",
        )

    async def completion_gate(self, raw_state: dict[str, Any]) -> dict[str, Any]:
        state = await self._load_state(raw_state)
        step = self._current_step(state)
        latest_review = state.review_batches[-1]
        if latest_review.step_id != step.step_id or latest_review.draft_version != (state.draft_artifact.version if state.draft_artifact else None):
            state.status = ThreadStatus.REVIEW_PENDING
        else:
            state.status = ThreadStatus.REVIEW_PENDING
        return await self._save_state(state, "completion_gate", "GRAPH_NODE_COMPLETED", {"status": state.status, "score": latest_review.total_score, "step_id": step.step_id})

    def route_after_revise_step_artifact(self, raw_state: dict[str, Any]) -> str:
        revised = bool(raw_state.get("revised"))
        if revised:
            return "critique_score"
        return "completion_gate"

    def route_after_completion_gate(self, raw_state: dict[str, Any]) -> str:
        state = ThreadState.model_validate(raw_state["state"])
        return "review_pending" if state.status == ThreadStatus.REVIEW_PENDING else "completed"
