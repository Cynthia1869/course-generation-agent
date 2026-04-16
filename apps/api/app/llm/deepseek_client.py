from __future__ import annotations

import os
from dataclasses import dataclass
import re
from textwrap import dedent
from typing import AsyncIterator

import httpx
import yaml
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.prompt_registry import PromptRegistry
from app.core.schemas import LLMProviderConfig
from app.core.settings import Settings


@dataclass(frozen=True)
class ProviderProfiles:
    chat: LLMProviderConfig
    review: LLMProviderConfig
    clarify: LLMProviderConfig | None = None
    extract: LLMProviderConfig | None = None
    generate: LLMProviderConfig | None = None
    improve: LLMProviderConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "clarify", self.clarify or self.chat)
        object.__setattr__(self, "extract", self.extract or self.chat)
        object.__setattr__(self, "generate", self.generate or self.chat)
        object.__setattr__(self, "improve", self.improve or self.generate or self.review or self.chat)


class RequirementExtractionResult(BaseModel):
    subject: str | None = None
    grade_level: str | None = None
    topic: str | None = None
    audience: str | None = None
    objective: str | None = None
    duration: str | None = None
    constraints: str | None = None
    course_positioning: str | None = None
    target_problem: str | None = None
    expected_result: str | None = None
    tone_style: str | None = None
    case_preferences: str | None = None
    case_variable: str | None = None
    case_flow: str | None = None
    failure_points: str | None = None
    application_scene: str | None = None
    script_requirements: str | None = None
    resource_requirements: str | None = None
    configuration_requirements: str | None = None


class DeepSeekClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.profile = self._load_profile()
        self.prompts = PromptRegistry(settings.prompt_root_dir)

    def _load_profile(self) -> ProviderProfiles:
        if self.settings.llm_config_file.exists():
            payload = yaml.safe_load(self.settings.llm_config_file.read_text(encoding="utf-8")) or {}
            providers = payload.get("providers", {})
            profiles = payload.get("profiles", {})

            def build_profile(name: str, fallback_temperature: float, fallback_order: tuple[str, ...] = ()) -> LLMProviderConfig:
                profile_payload = profiles.get(name) or {}
                if "model" not in profile_payload:
                    for fallback_name in fallback_order:
                        candidate = profiles.get(fallback_name) or {}
                        if "model" in candidate:
                            profile_payload = candidate
                            break
                provider_name = profile_payload.get("provider", payload.get("default_provider", "deepseek"))
                provider_payload = providers.get(provider_name, {})
                return LLMProviderConfig(
                    provider=provider_name,
                    model=profile_payload["model"],
                    temperature=float(profile_payload.get("temperature", fallback_temperature)),
                    api_base_env=provider_payload.get("api_base_env"),
                    api_key_env=provider_payload.get("api_key_env"),
                    base_url=provider_payload.get("base_url"),
                )

            return ProviderProfiles(
                chat=build_profile("chat", 0.4),
                review=build_profile("review", 0.1, fallback_order=("generate", "chat")),
                clarify=build_profile("clarify", 0.2, fallback_order=("chat",)),
                extract=build_profile("extract", 0.0, fallback_order=("chat",)),
                generate=build_profile("generate", 0.3, fallback_order=("review", "chat")),
                improve=build_profile("improve", 0.3, fallback_order=("generate", "review", "chat")),
            )

        payload = yaml.safe_load(self.settings.deepseek_config_file.read_text(encoding="utf-8")) or {}
        return ProviderProfiles(
            chat=LLMProviderConfig(
                provider="deepseek",
                model=payload["chat_model"],
                temperature=float(payload.get("chat_temperature", 0.4)),
                api_base_env="DEEPSEEK_BASE_URL",
                api_key_env="DEEPSEEK_API_KEY",
                base_url=self.settings.deepseek_base_url,
            ),
            clarify=LLMProviderConfig(
                provider="deepseek",
                model=payload.get("clarify_model", payload["chat_model"]),
                temperature=float(payload.get("clarify_temperature", 0.2)),
                api_base_env="DEEPSEEK_BASE_URL",
                api_key_env="DEEPSEEK_API_KEY",
                base_url=self.settings.deepseek_base_url,
            ),
            extract=LLMProviderConfig(
                provider="deepseek",
                model=payload.get("extract_model", payload["chat_model"]),
                temperature=float(payload.get("extract_temperature", 0.0)),
                api_base_env="DEEPSEEK_BASE_URL",
                api_key_env="DEEPSEEK_API_KEY",
                base_url=self.settings.deepseek_base_url,
            ),
            generate=LLMProviderConfig(
                provider="deepseek",
                model=payload.get("generate_model", payload["chat_model"]),
                temperature=float(payload.get("generate_temperature", payload.get("chat_temperature", 0.4))),
                api_base_env="DEEPSEEK_BASE_URL",
                api_key_env="DEEPSEEK_API_KEY",
                base_url=self.settings.deepseek_base_url,
            ),
            review=LLMProviderConfig(
                provider="deepseek",
                model=payload.get("review_model", payload["chat_model"]),
                temperature=float(payload.get("review_temperature", 0.1)),
                api_base_env="DEEPSEEK_BASE_URL",
                api_key_env="DEEPSEEK_API_KEY",
                base_url=self.settings.deepseek_base_url,
            ),
            improve=LLMProviderConfig(
                provider="deepseek",
                model=payload.get("improve_model", payload.get("review_model", payload["chat_model"])),
                temperature=float(payload.get("improve_temperature", 0.3)),
                api_base_env="DEEPSEEK_BASE_URL",
                api_key_env="DEEPSEEK_API_KEY",
                base_url=self.settings.deepseek_base_url,
            ),
        )

    def get_profile(self, action: str) -> LLMProviderConfig:
        try:
            return getattr(self.profile, action)
        except AttributeError as exc:
            raise KeyError(f"Unknown llm profile action: {action}") from exc

    def get_profile_name(self, action: str) -> str:
        self.get_profile(action)
        return action

    def can_use_remote_llm(self, action: str = "chat") -> bool:
        if self.settings.app_env == "test":
            return False
        return bool(self._resolve_api_key(self.get_profile(action)))

    def _resolve_api_key(self, profile: LLMProviderConfig) -> str | None:
        if profile.api_key_env:
            return os.getenv(profile.api_key_env)
        return self.settings.deepseek_api_key

    def _resolve_base_url(self, profile: LLMProviderConfig) -> str:
        if profile.api_base_env and os.getenv(profile.api_base_env):
            return os.getenv(profile.api_base_env) or profile.base_url or self.settings.deepseek_base_url
        return profile.base_url or self.settings.deepseek_base_url

    def _build_chat_model(self, profile: LLMProviderConfig) -> ChatOpenAI:
        # trust_env=False prevents httpx from picking up SOCKS/HTTP proxy env vars
        return ChatOpenAI(
            model=profile.model,
            temperature=profile.temperature,
            api_key=self._resolve_api_key(profile),
            base_url=self._resolve_base_url(profile),
            http_client=httpx.Client(trust_env=False),
            http_async_client=httpx.AsyncClient(trust_env=False),
        )

    async def stream_markdown(self, context: dict) -> AsyncIterator[str]:
        if not self.can_use_remote_llm():
            for chunk in self._split_chunks(self._fallback_markdown(context)):
                yield chunk
            return

        model = self._build_chat_model(self.get_profile("generate"))
        prompt = self.prompts.render_by_id(
            "generate.legacy_full_draft",
            decision_summary=context["decision_summary"],
            slot_summary=context["slot_summary"],
            source_summary=context["source_summary"],
            example_reference=context["example_reference"],
            constraint_summary=context.get("constraint_summary", "无额外约束"),
        )
        async for chunk in model.astream(prompt):
            text = self._response_text(chunk)
            if text:
                yield text

    async def stream_step_markdown(self, context: dict) -> AsyncIterator[str]:
        if not self.can_use_remote_llm():
            for chunk in self._split_chunks(self._fallback_step_markdown(context)):
                yield chunk
            return

        model = self._build_chat_model(self.get_profile("generate"))
        prompt = self.prompts.render_by_id(
            context["prompt_id"],
            step_label=context["step_label"],
            step_scope=context["step_scope"],
            allowed_input_layers=context["allowed_input_layers"],
            forbidden_input_layers=context["forbidden_input_layers"],
            output_contract=context["output_contract"],
            generation_goal=context["generation_goal"],
            structured_inputs=context["structured_inputs"],
            confirmed_artifacts=context["confirmed_artifacts"],
            source_summary=context["source_summary"],
        )
        async for chunk in model.astream(prompt):
            text = self._response_text(chunk)
            if text:
                yield text

    async def ask_clarification(self, context: dict) -> str:
        if not self.can_use_remote_llm():
            item = context["missing_requirement"]
            return "为了继续制课，我还需要你补充这一个关键信息：\n" + f"- {item['label']}：{item['prompt_hint']}"

        model = self._build_chat_model(self.get_profile("clarify"))
        prompt = self.prompts.render_by_id(
            context.get("prompt_id", "clarify.course_title"),
            step_label=context.get("step_label", "当前步骤"),
            step_scope=context.get("step_scope", "只允许处理当前步骤"),
            allowed_input_layers=context.get("allowed_input_layers", "当前步骤结构化输入、上传资料摘要"),
            forbidden_input_layers=context.get("forbidden_input_layers", "原始聊天消息、未来步骤内容、未确认产物"),
            output_contract=context.get("output_contract", "只追问当前步骤一个缺失项"),
            allowed_scope=context.get("allowed_scope", "仅限当前步骤信息"),
            forbidden_scope=context.get("forbidden_scope", "不要追问其他步骤内容"),
            structured_inputs=context["structured_inputs"],
            missing_requirements=f"- {context['missing_requirement']['label']}：{context['missing_requirement']['prompt_hint']}",
        )
        response = await model.ainvoke(prompt)
        return self._response_text(response)

    async def stream_clarification(self, context: dict) -> AsyncIterator[str]:
        if not self.can_use_remote_llm():
            item = context["missing_requirement"]
            text = (
                f"当前在“{context.get('step_label', '当前步骤')}”这一步，为了继续往下走，我只需要你补充一个信息：\n"
                + f"- {item['label']}：{item['prompt_hint']}"
            )
            for chunk in self._split_chunks(text, chunk_size=24):
                yield chunk
            return

        model = self._build_chat_model(self.get_profile("clarify"))
        prompt = self.prompts.render_by_id(
            context["prompt_id"],
            step_label=context.get("step_label", "当前步骤"),
            step_scope=context.get("step_scope", "只允许处理当前步骤"),
            allowed_input_layers=context.get("allowed_input_layers", "当前步骤结构化输入、上传资料摘要"),
            forbidden_input_layers=context.get("forbidden_input_layers", "原始聊天消息、未来步骤内容、未确认产物"),
            output_contract=context.get("output_contract", "只追问当前步骤一个缺失项"),
            allowed_scope=context.get("allowed_scope", "仅限当前步骤信息"),
            forbidden_scope=context.get("forbidden_scope", "不要追问其他步骤内容"),
            structured_inputs=context["structured_inputs"],
            missing_requirements=f"- {context['missing_requirement']['label']}：{context['missing_requirement']['prompt_hint']}",
        )
        async for chunk in model.astream(prompt):
            text = self._response_text(chunk)
            if text:
                yield text

    async def extract_requirements(
        self,
        *,
        latest_user_message: str,
        known_requirements: dict[str, str | None],
        requirement_defs: list[dict],
    ) -> dict[str, str | None]:
        if not latest_user_message.strip():
            return {}
        if not self.can_use_remote_llm():
            return self._fallback_extract_requirements(latest_user_message)

        model = self._build_chat_model(self.get_profile("extract"))
        prompt = self.prompts.render_by_id(
            "extract.requirements",
            requirement_defs="\n".join(f"- {item['slot_id']}: {item['label']}，{item['prompt_hint']}" for item in requirement_defs),
            known_requirements="\n".join(f"- {key}: {value}" for key, value in known_requirements.items()) or "无",
            latest_user_message=latest_user_message,
        )
        structured = model.with_structured_output(RequirementExtractionResult)
        result = await structured.ainvoke(prompt)
        payload = result.model_dump()
        return {key: value for key, value in payload.items() if value}

    def _fallback_extract_requirements(self, latest_user_message: str) -> dict[str, str | None]:
        text = latest_user_message.strip()
        extracted: dict[str, str | None] = {}

        subject_match = re.search(r"(数学|物理|英语|语文|化学|生物|历史|地理)", text)
        if subject_match:
            extracted["subject"] = subject_match.group(1)

        grade_match = re.search(r"(初一|初二|初三|高一|高二|高三|七年级|八年级|九年级)", text)
        if grade_match:
            extracted["grade_level"] = grade_match.group(1)

        audience_match = re.search(r"(初中生|高中生|小学生|零基础学员)", text)
        if audience_match:
            extracted["audience"] = audience_match.group(1)

        topic_match = re.search(r"(三角函数|一次函数|二次函数|几何证明|勾股定理|圆|概率|统计)", text)
        if topic_match:
            extracted["topic"] = topic_match.group(1)

        if "入门" in text:
            extracted["course_positioning"] = "入门课"
        elif "进阶" in text:
            extracted["course_positioning"] = "进阶课"
        elif "训练营" in text:
            extracted["course_positioning"] = "训练营"

        if "实操" in text or "带练" in text:
            extracted["tone_style"] = "实操带练"
        elif "讲解" in text:
            extracted["tone_style"] = "知识讲解"
        elif "口语化" in text:
            extracted["tone_style"] = "口语化"

        problem_match = re.search(r"(?:解决|问题是)([^，。,；;\n]+)", text)
        if problem_match:
            extracted["target_problem"] = problem_match.group(1).strip()

        result_match = re.search(r"(?:学完能|结果是)([^，。,；;\n]+)", text)
        if result_match:
            extracted["expected_result"] = result_match.group(1).strip()

        case_match = re.search(r"案例(?:要求|偏好)?是([^。；;\n]+)", text)
        if case_match:
            extracted["case_preferences"] = case_match.group(1).strip()

        variable_match = re.search(r"变量(?:是)?([^。；;\n]+)", text)
        if variable_match:
            extracted["case_variable"] = variable_match.group(1).strip()

        flow_match = re.search(r"流程(?:是)?([^。；;\n]+)", text)
        if flow_match:
            extracted["case_flow"] = flow_match.group(1).strip()

        failure_match = re.search(r"(?:失败点|踩坑点)(?:是)?([^。；;\n]+)", text)
        if failure_match:
            extracted["failure_points"] = failure_match.group(1).strip()

        scene_match = re.search(r"场景(?:是)?([^。；;\n]+)", text)
        if scene_match:
            extracted["application_scene"] = scene_match.group(1).strip()

        script_match = re.search(r"逐字稿(?:要求)?是([^。；;\n]+)", text)
        if script_match:
            extracted["script_requirements"] = script_match.group(1).strip()

        material_match = re.search(r"素材(?:清单)?(?:要求|范围)?是([^。；;\n]+)", text)
        if material_match:
            extracted["resource_requirements"] = material_match.group(1).strip()

        config_match = re.search(r"配置(?:要求|需求)?是([^。；;\n]+)", text)
        if config_match:
            extracted["configuration_requirements"] = config_match.group(1).strip()

        return extracted

    async def review_markdown(
        self,
        *,
        prompt_id: str,
        markdown: str,
        rubric: list[dict],
        threshold: float,
        step_label: str = "当前步骤产物",
        step_scope: str = "只允许评审当前步骤",
        allowed_input_layers: str = "当前步骤结构化输入、已确认前序产物、上传资料摘要、当前步骤产物",
        forbidden_input_layers: str = "未来步骤内容、未确认产物、reasoning 内容、原始聊天消息",
        forbidden_topics: str = "无",
    ) -> dict:
        if not self.can_use_remote_llm():
            return self._fallback_review(markdown=markdown, rubric=rubric, threshold=threshold)

        model = self._build_chat_model(self.get_profile("review"))
        rubric_text = "\n".join(
            f"- {item['criterion_id']}: {item['name']} ({item['description']})"
            for item in rubric
        )
        prompt = self.prompts.render_by_id(
            prompt_id,
            step_label=step_label,
            step_scope=step_scope,
            allowed_input_layers=allowed_input_layers,
            forbidden_input_layers=forbidden_input_layers,
            output_contract="只评审当前步骤当前版本，不改写前序已确认内容，不把未来步骤当成缺陷。",
            threshold=threshold,
            rubric_text=rubric_text,
            markdown=markdown,
            forbidden_topics=forbidden_topics,
        )
        structured = model.with_structured_output(dict)
        return await structured.ainvoke(prompt)

    async def improve_markdown(
        self,
        *,
        prompt_id: str,
        markdown: str,
        approved_changes: list[str],
        structured_inputs: str,
        confirmed_artifacts: str = "暂无已确认前序产物",
        source_summary: str = "无上传资料",
        source_version: int | None = None,
        revision_goal: str | None = None,
        step_label: str = "当前步骤产物",
        step_scope: str = "只允许修订当前步骤",
    ) -> str:
        if not approved_changes:
            return markdown
        if not self.can_use_remote_llm():
            lines = "\n".join(f"- {item}" for item in approved_changes)
            extra = f"\n基础版本：v{source_version}" if source_version else ""
            goal = f"\n修订目标：{revision_goal}" if revision_goal else ""
            return f"{markdown}\n\n## 自动优化说明\n\n本轮自动优化已执行：\n{lines}{extra}{goal}"

        model = self._build_chat_model(self.get_profile("improve"))
        prompt = self.prompts.render_by_id(
            prompt_id,
            step_label=step_label,
            step_scope=step_scope,
            allowed_input_layers="当前步骤结构化输入、已确认前序产物、上传资料摘要、当前步骤产物、当前轮改进要求",
            forbidden_input_layers="未来步骤内容、未确认产物、reasoning 内容、原始聊天消息",
            output_contract="只返回当前步骤修订后的完整 Markdown。",
            structured_inputs=structured_inputs,
            confirmed_artifacts=confirmed_artifacts,
            source_summary=source_summary,
            approved_changes="\n".join(f"- {item}" for item in approved_changes),
            markdown=markdown,
            source_version=source_version or "未指定",
            revision_goal=revision_goal or "根据反馈生成更好的新版本",
        )
        response = await model.ainvoke(prompt)
        return self._response_text(response)

    def _response_text(self, response: object) -> str:
        # DeepSeek reasoning models may expose reasoning content via side channels such as
        # additional_kwargs. Business state must only persist the final answer content.
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts)
        return str(content)

    def _fallback_markdown(self, context: dict) -> str:
        topic = context["slots"].get("topic", "待补充主题")
        audience = context["slots"].get("audience", "目标学员")
        objective = context["slots"].get("objective", "完成本节课目标")
        return dedent(
            f"""
            # 单课框架

            本节课解决的核心问题：围绕“{topic}”，帮助{audience}达成“{objective}”。

            ## 你将学会

            - 掌握与“{topic}”相关的核心方法和底层逻辑
            - 能够在真实任务中复用标准化步骤
            - 能够根据反馈继续优化结果

            ## 案例设计

            ### 【案例 1】快速上手案例
            目标：学员能够完成最小可运行结果，并理解基本操作路径。

            ### 【案例 2】业务加压案例
            目标：学员能够在更复杂约束下复用方法，并根据反馈调整输出。

            ### 【案例 3】复盘沉淀案例
            目标：学员能够复盘关键决策，并把方法迁移到新的业务题目。

            ## 整课主线

            先让学员做出结果，再在第二个案例中承受更复杂的条件，最后通过复盘把动作、认知和思维三层目标收拢成稳定方法。

            ## 逐字稿

            大家这节课只盯一件事：{topic}。我们先不讲大背景，先把第一个结果做出来。

            在第一个案例里，你先跟着完成最小动作，拿到一个可用结果。做完以后，我们再解释为什么这样设计。

            进入第二个案例，我们把业务条件收紧。现在不是为了再做一遍，而是为了学会在约束变化时怎么判断、怎么改。

            到第三个案例，我们不再只看输出本身，而是总结你面对类似问题时的下手顺序、判断标准和修正方法。
            """
        ).strip()

    def _fallback_review(self, *, markdown: str, rubric: list[dict], threshold: float) -> dict:
        sections = {
            "core-problem": "本节课解决的核心问题" in markdown,
            "case-design": "案例" in markdown and "目标：" in markdown,
            "mainline": "整课主线" in markdown,
            "script-quality": "逐字稿" in markdown,
            "source-grounding": len(markdown) > 400,
        }
        criteria = []
        suggestions = []
        total_weight = sum(item["weight"] for item in rubric)
        weighted_sum = 0.0
        for item in rubric:
            present = sections.get(item["criterion_id"], False)
            score = 8.5 if present else 5.0
            reason = "结构满足要求。" if present else "该部分信息不足或未明确展开。"
            criteria.append(
                {
                    "criterion_id": item["criterion_id"],
                    "name": item["name"],
                    "weight": item["weight"],
                    "score": score,
                    "max_score": item["max_score"],
                    "reason": reason,
                }
            )
            weighted_sum += score * item["weight"]
            if not present:
                suggestions.append(
                    {
                        "criterion_id": item["criterion_id"],
                        "problem": f"{item['name']}未充分体现。",
                        "suggestion": f"补强“{item['name']}”相关段落，使其满足 rubric。",
                        "evidence_span": item["name"],
                        "severity": "high",
                    }
                )
        total_score = round(weighted_sum / total_weight, 2)
        if total_score >= threshold and not suggestions:
            suggestions.append(
                {
                    "criterion_id": "script-quality",
                    "problem": "整体可通过，但还可以进一步提升表达贴合度。",
                    "suggestion": "检查逐字稿是否更贴近真实授课语气。",
                    "evidence_span": "逐字稿",
                    "severity": "low",
                }
            )
        return {"total_score": total_score, "criteria": criteria, "suggestions": suggestions}

    def _fallback_step_markdown(self, context: dict) -> str:
        return dedent(
            f"""
            # {context['step_label']}

            本步骤目标：{context['generation_goal']}

            ## 当前步骤边界

            {context['step_scope']}

            ## 当前步骤结构化输入

            {context['structured_inputs'] or '暂无当前步骤结构化输入'}

            ## 已确认前序产物

            {context['confirmed_artifacts'] or '暂无已确认前序产物'}

            ## 上传资料摘要

            {context['source_summary'] or '无上传资料'}
            """
        ).strip()

    def _split_chunks(self, text: str, chunk_size: int = 120) -> list[str]:
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
