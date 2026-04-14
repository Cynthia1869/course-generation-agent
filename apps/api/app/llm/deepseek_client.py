from __future__ import annotations

from dataclasses import dataclass
import re
from textwrap import dedent
from typing import AsyncIterator

import httpx
import yaml
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.prompt_registry import PromptRegistry
from app.core.settings import Settings


@dataclass(frozen=True)
class DeepSeekProfile:
    chat_model: str
    review_model: str
    chat_temperature: float
    review_temperature: float


class RequirementExtractionResult(BaseModel):
    subject: str | None = None
    grade_level: str | None = None
    topic: str | None = None
    audience: str | None = None
    objective: str | None = None
    duration: str | None = None
    constraints: str | None = None


class DeepSeekClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.profile = self._load_profile()
        self.prompts = PromptRegistry(settings.prompt_root_dir)

    def _load_profile(self) -> DeepSeekProfile:
        payload = yaml.safe_load(self.settings.deepseek_config_file.read_text(encoding="utf-8"))
        return DeepSeekProfile(
            chat_model=payload["chat_model"],
            review_model=payload["review_model"],
            chat_temperature=float(payload.get("chat_temperature", 0.4)),
            review_temperature=float(payload.get("review_temperature", 0.1)),
        )

    def can_use_remote_llm(self) -> bool:
        if self.settings.app_env == "test":
            return False
        return bool(self.settings.deepseek_api_key)

    def _build_chat_model(self, *, model: str, temperature: float) -> ChatOpenAI:
        # trust_env=False prevents httpx from picking up SOCKS/HTTP proxy env vars
        return ChatOpenAI(
            model=model,
            temperature=temperature,
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            http_client=httpx.Client(trust_env=False),
            http_async_client=httpx.AsyncClient(trust_env=False),
        )

    async def stream_markdown(self, context: dict) -> AsyncIterator[str]:
        if not self.can_use_remote_llm():
            for chunk in self._split_chunks(self._fallback_markdown(context)):
                yield chunk
            return

        model = self._build_chat_model(
            model=self.profile.chat_model,
            temperature=self.profile.chat_temperature,
        )
        prompt = self.prompts.render(
            "deepseek/generate_markdown.md",
            decision_summary=context["decision_summary"],
            slot_summary=context["slot_summary"],
            source_summary=context["source_summary"],
            example_reference=context["example_reference"],
        )
        async for chunk in model.astream(prompt):
            text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
            if text:
                yield text

    async def ask_clarification(self, context: dict) -> str:
        if not self.can_use_remote_llm():
            prompts = [f"{item['label']}：{item['prompt_hint']}" for item in context["missing_requirements"]]
            return "为了继续制课，我还需要你补充这些信息：\n" + "\n".join(f"- {line}" for line in prompts)

        model = self._build_chat_model(
            model=self.profile.chat_model,
            temperature=0.2,
        )
        prompt = self.prompts.render(
            "deepseek/clarify_requirements.md",
            slot_summary=context["slot_summary"],
            missing_requirements="\n".join(
                f"- {item['label']}：{item['prompt_hint']}" for item in context["missing_requirements"]
            ),
        )
        response = await model.ainvoke(prompt)
        return response.content if isinstance(response.content, str) else str(response.content)

    async def stream_clarification(self, context: dict) -> AsyncIterator[str]:
        if not self.can_use_remote_llm():
            prompts = [f"{item['label']}：{item['prompt_hint']}" for item in context["missing_requirements"]]
            text = "为了继续制课，我还需要你补充这些信息：\n" + "\n".join(f"- {line}" for line in prompts)
            for chunk in self._split_chunks(text, chunk_size=24):
                yield chunk
            return

        model = self._build_chat_model(
            model=self.profile.chat_model,
            temperature=0.2,
        )
        prompt = self.prompts.render(
            "deepseek/clarify_requirements.md",
            slot_summary=context["slot_summary"],
            missing_requirements="\n".join(
                f"- {item['label']}：{item['prompt_hint']}" for item in context["missing_requirements"]
            ),
        )
        async for chunk in model.astream(prompt):
            text = chunk.content if isinstance(chunk.content, str) else str(chunk.content)
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

        model = self._build_chat_model(
            model=self.profile.chat_model,
            temperature=0.0,
        )
        prompt = self.prompts.render(
            "deepseek/extract_requirements.md",
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

        return extracted

    async def review_markdown(self, *, markdown: str, rubric: list[dict], threshold: float) -> dict:
        if not self.can_use_remote_llm():
            return self._fallback_review(markdown=markdown, rubric=rubric, threshold=threshold)

        model = self._build_chat_model(
            model=self.profile.review_model,
            temperature=self.profile.review_temperature,
        )
        rubric_text = "\n".join(
            f"- {item['criterion_id']}: {item['name']} ({item['description']})"
            for item in rubric
        )
        prompt = self.prompts.render(
            "deepseek/review_markdown.md",
            threshold=threshold,
            rubric_text=rubric_text,
            markdown=markdown,
        )
        structured = model.with_structured_output(dict)
        return await structured.ainvoke(prompt)

    async def improve_markdown(self, *, markdown: str, approved_changes: list[str], context_summary: str) -> str:
        if not approved_changes:
            return markdown
        if not self.can_use_remote_llm():
            lines = "\n".join(f"- {item}" for item in approved_changes)
            return f"{markdown}\n\n## 自动优化说明\n\n本轮自动优化已执行：\n{lines}"

        model = self._build_chat_model(
            model=self.profile.chat_model,
            temperature=0.3,
        )
        prompt = self.prompts.render(
            "deepseek/improve_markdown.md",
            context_summary=context_summary,
            approved_changes="\n".join(f"- {item}" for item in approved_changes),
            markdown=markdown,
        )
        response = await model.ainvoke(prompt)
        return response.content if isinstance(response.content, str) else str(response.content)

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

    def _split_chunks(self, text: str, chunk_size: int = 120) -> list[str]:
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
