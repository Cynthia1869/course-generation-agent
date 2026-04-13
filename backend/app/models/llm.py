from __future__ import annotations

from textwrap import dedent

from langchain_openai import ChatOpenAI

from app.config import Settings


class ModelGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def can_use_remote_llm(self) -> bool:
        return bool(self.settings.openai_api_key)

    def build_chat_model(self, *, model_name: str, temperature: float = 0.3) -> ChatOpenAI:
        kwargs = {
            "model": model_name,
            "temperature": temperature,
            "api_key": self.settings.openai_api_key,
        }
        if self.settings.openai_base_url:
            kwargs["base_url"] = self.settings.openai_base_url
        return ChatOpenAI(**kwargs)

    async def generate_markdown(self, context: dict) -> str:
        if not self.can_use_remote_llm():
            return self._fallback_markdown(context)

        model = self.build_chat_model(model_name=self.settings.default_gen_model, temperature=0.4)
        prompt = dedent(
            """
            你是企业内训课程设计助手。请根据已确认需求，生成一份中文 Markdown 主稿。
            必须包含：
            1. 单课框架
            2. 1-3 个案例，每个案例有目标
            3. 整课主线
            4. 逐字稿
            如果主题涉及 AI / LLM 教学，请按需加入“随机性应对”章节。

            已确认需求:
            {decision_summary}

            需求槽位:
            {slot_summary}

            资料摘要:
            {source_summary}
            """
        ).format(
            decision_summary=context["decision_summary"],
            slot_summary=context["slot_summary"],
            source_summary=context["source_summary"],
        )
        response = await model.ainvoke(prompt)
        return response.content if isinstance(response.content, str) else str(response.content)

    async def ask_clarification(self, context: dict) -> str:
        if not self.can_use_remote_llm():
            missing = ", ".join(context["missing_slots"])
            return f"为了继续制课，我还需要你补充这些信息：{missing}。请尽量给出具体业务场景、目标学员和课程目标。"

        model = self.build_chat_model(model_name=self.settings.default_gen_model, temperature=0.2)
        prompt = dedent(
            """
            你是一个严谨的制课需求访谈助手。当前信息还不完整，请用中文向用户追问。
            只输出一段自然对话式追问，不要列格式说明。

            已有需求:
            {slot_summary}

            缺失信息:
            {missing_slots}
            """
        ).format(
            slot_summary=context["slot_summary"],
            missing_slots=", ".join(context["missing_slots"]),
        )
        response = await model.ainvoke(prompt)
        return response.content if isinstance(response.content, str) else str(response.content)

    async def review_markdown(self, *, markdown: str, rubric: list[dict], threshold: float) -> dict:
        if not self.can_use_remote_llm():
            return self._fallback_review(markdown=markdown, rubric=rubric, threshold=threshold)

        model = self.build_chat_model(model_name=self.settings.default_review_model, temperature=0.1)
        rubric_text = "\n".join(
            f"- {item['criterion_id']}: {item['name']} ({item['description']})"
            for item in rubric
        )
        prompt = dedent(
            """
            请你作为课程内容评审员，严格按 rubric 评分，并返回 JSON。
            JSON 结构:
            {
              "total_score": 0,
              "criteria": [
                {
                  "criterion_id": "",
                  "name": "",
                  "weight": 0,
                  "score": 0,
                  "max_score": 10,
                  "reason": ""
                }
              ],
              "suggestions": [
                {
                  "criterion_id": "",
                  "problem": "",
                  "suggestion": "",
                  "evidence_span": "",
                  "severity": "low|medium|high"
                }
              ]
            }
            阈值是 {threshold}。只返回 JSON。

            Rubric:
            {rubric_text}

            Markdown:
            {markdown}
            """
        ).format(threshold=threshold, rubric_text=rubric_text, markdown=markdown)
        structured = model.with_structured_output(dict)
        return await structured.ainvoke(prompt)

    def _fallback_markdown(self, context: dict) -> str:
        topic = context["slots"].get("topic", "待补充主题")
        audience = context["slots"].get("audience", "企业学员")
        objective = context["slots"].get("objective", "完成本节课目标")
        return dedent(
            f"""
            # 单课框架

            本节课解决的核心问题：围绕“{topic}”，帮助{audience}在真实业务场景中达成“{objective}”。

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
