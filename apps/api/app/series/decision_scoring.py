from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel, Field

from app.llm.deepseek_client import DeepSeekClient
from app.series.scoring import SeriesReviewReport, SeriesSuggestion, parse_framework_markdown, score_framework_markdown

PASS_THRESHOLD = 80.0
PRACTICE_KEYWORDS = {"实战", "实践", "案例", "应用", "演练", "训练", "项目", "落地", "复盘", "工作流"}
FOUNDATION_KEYWORDS = {"基础", "入门", "理解", "认知", "定位", "框架", "概念", "准备"}
FORBIDDEN_DOMAIN_KEYWORDS = {"医学", "医疗", "医美", "诊断", "治疗", "患者", "处方", "临床"}
HIGH_RISK_DOMAIN_KEYWORDS = {"法务", "保险", "公关", "舆情", "医疗", "医美", "合规"}
BOUNDARY_KEYWORDS = {"边界", "风险", "合规", "审批", "人工", "校验", "免责声明", "安全"}
EXPLICIT_SAFETY_GUARDRAILS = {"人工复核", "人工审核", "人工把关", "免责声明", "使用限制", "边界", "校验"}
OVERSIZED_TOPIC_KEYWORDS = {"一人公司", "产品线", "经营分析", "组织管理", "完整工作流", "全流程", "系统搭建"}
UNDERSIZED_TOPIC_KEYWORDS = {"合同初审", "会议纪要", "简历初筛", "JD生成", "口播脚本", "切片文案"}
MIXED_TOPIC_PAIRS = [("招聘", "周报"), ("招聘", "组织管理"), ("招聘", "团队管理")]
CORE_WORKFLOW_REQUIREMENTS = (
    (
        {"漫剧", "漫画剧", "AI漫剧"},
        {"视频生成", "转视频", "动画生成", "视频制作", "视频剪辑", "运镜", "动态镜头", "镜头衔接", "成片输出"},
        "课程承诺的是“漫剧”交付，但课程里没有覆盖视频生成、动态镜头或成片环节，核心工作流不闭合。",
    ),
)


class LLMSeriesReviewRefinement(BaseModel):
    total_score: float = Field(ge=0, le=100)
    summary: str
    additional_suggestions: list[str] = Field(default_factory=list)


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _joined_lesson_text(framework) -> str:
    return " ".join(f"{lesson.title} {lesson.summary}" for lesson in framework.lessons)


def _late_lesson_text(framework) -> str:
    lessons = framework.lessons[max(0, len(framework.lessons) // 2) :]
    return " ".join(f"{lesson.title} {lesson.summary}" for lesson in lessons)


def _build_decision_suggestions(markdown_text: str) -> tuple[list[SeriesSuggestion], float]:
    framework = parse_framework_markdown(markdown_text)
    lesson_text = _joined_lesson_text(framework)
    late_lesson_text = _late_lesson_text(framework)
    full_text = " ".join([framework.course_name, framework.target_user, framework.learner_current_state, framework.learner_expected_state, framework.mindset_shift, framework.core_problem, framework.application_scenario, lesson_text])
    suggestions: list[SeriesSuggestion] = []
    penalty = 0.0

    if _contains_any(full_text, FORBIDDEN_DOMAIN_KEYWORDS):
        suggestions.append(SeriesSuggestion("安全边界", "课程涉及医疗诊断等高风险领域，这类场景不适合直接作为通用系列课模板交付。", "缩窄到低风险的流程型教学目标，或明确加入人工审核与免责声明边界。", "课程名称/核心问题", "high"))
        penalty += 25.0
    elif _contains_any(full_text, HIGH_RISK_DOMAIN_KEYWORDS) and not _contains_any(full_text, EXPLICIT_SAFETY_GUARDRAILS):
        suggestions.append(SeriesSuggestion("边界与约束清晰度", "课程涉及高风险业务场景，但没有明确人工复核或边界说明。", "补上风险边界、人工复核节点和使用限制，避免把高风险判断自动化。", "课程应用场景", "high"))
        penalty += 10.0

    if any(left in full_text and right in full_text for left, right in MIXED_TOPIC_PAIRS):
        suggestions.append(SeriesSuggestion("内容逻辑性", "课程主题混入了多个跨度过大的目标，容易导致系列课主线发散。", "先收敛成一个核心结果，再围绕这个结果安排认知、方法和实战递进。", "课程名称/课程框架", "high"))
        penalty += 12.0

    if _contains_any(full_text, OVERSIZED_TOPIC_KEYWORDS) and len(framework.lessons) <= 6:
        suggestions.append(SeriesSuggestion("课程规模合理性", "主题范围偏大，但课时规模偏小，容易出现课程承诺过多而无法讲透。", "缩窄交付结果，或把系列课拆成更清晰的阶段性主题。", "课程名称/课程框架", "medium"))
        penalty += 6.0

    if _contains_any(full_text, UNDERSIZED_TOPIC_KEYWORDS) and len(framework.lessons) >= 8:
        suggestions.append(SeriesSuggestion("课程规模合理性", "主题颗粒度偏小，但课时规模偏大，容易出现信息注水。", "合并重复课时，或扩大目标结果，让每节课承担清晰任务。", "课程框架", "medium"))
        penalty += 5.0

    if framework.lessons:
        first_text = f"{framework.lessons[0].title} {framework.lessons[0].summary}"
        if _contains_any(first_text, PRACTICE_KEYWORDS) and not _contains_any(first_text, FOUNDATION_KEYWORDS):
            suggestions.append(SeriesSuggestion("内容逻辑性", "课程一开始就进入应用或实战，但基础认知没有先立住，难度跃迁会偏陡。", "把前 1 到 2 课调整成认知、方法或判断框架铺垫，再进入案例和应用。", "前两课安排", "high"))
            penalty += 8.0

    if not _contains_any(late_lesson_text, PRACTICE_KEYWORDS):
        suggestions.append(SeriesSuggestion("实战性", "后半段缺少明显的案例、项目或复盘，系列课更像知识讲解而不是带结果的训练。", "把最后三分之一课时改成案例拆解、完整演练或项目复盘，让前面的方法真正落地。", "课程框架", "high"))
        penalty += 12.0

    for topic_keywords, workflow_keywords, reason in CORE_WORKFLOW_REQUIREMENTS:
        if _contains_any(full_text, topic_keywords) and not _contains_any(full_text, workflow_keywords):
            suggestions.append(SeriesSuggestion("核心工作流闭环", reason, "补上完成该交付结果必须依赖的关键环节，否则课程无法形成完整工作流。", "课程框架", "high"))
            penalty += 12.0

    unique: list[SeriesSuggestion] = []
    seen = set()
    for item in suggestions:
        key = (item.criterion_id, item.problem)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:5], penalty


async def _llm_refine_series_review(markdown_text: str, base_report: SeriesReviewReport, deepseek: DeepSeekClient) -> SeriesReviewReport:
    if not deepseek.can_use_remote_llm("review"):
        return base_report
    try:
        model = deepseek._build_chat_model(deepseek.get_profile("review"))
        structured = model.with_structured_output(LLMSeriesReviewRefinement, method="function_calling")
        result = await structured.ainvoke(
            [
                ("system", "你是一名严格的系列课程评审顾问。请根据课程框架和当前评分结果，给出更稳妥的最终分数、总结，以及最多 3 条额外建议。评分标准强调：主题大小和课时匹配、内容递进、后半段实战、真实场景落地、风险边界。如果涉及明显高风险场景或结构性问题，分数不应高于 79。"),
                ("human", f"课程框架：\n{markdown_text}\n\n当前规则评分：{base_report.total_score}\n当前总结：{base_report.summary}\n当前建议：\n" + "\n".join(f"- {item.problem} -> {item.suggestion}" for item in base_report.suggestions)),
            ]
        )
    except Exception:
        return base_report
    merged_suggestions = list(base_report.suggestions)
    for item in result.additional_suggestions[:3]:
        merged_suggestions.append(SeriesSuggestion("llm_decision", "评分决策补充意见", item, "评分补充", "medium"))
    return replace(base_report, total_score=round(result.total_score, 2), summary=result.summary.strip() or base_report.summary, suggestions=merged_suggestions[:5])


async def score_series_framework_markdown(markdown_text: str, deepseek: DeepSeekClient) -> SeriesReviewReport:
    base_report = score_framework_markdown(markdown_text)
    rule_suggestions, penalty = _build_decision_suggestions(markdown_text)
    merged_suggestions = list(base_report.suggestions)
    existing = {(item.criterion_id, item.problem) for item in merged_suggestions}
    for item in rule_suggestions:
        if (item.criterion_id, item.problem) not in existing:
            merged_suggestions.append(item)
    total_score = max(0.0, round(base_report.total_score - penalty, 2))
    if any(item.criterion_id == "安全边界" for item in rule_suggestions):
        total_score = min(total_score, 69.0)
    elif any(item.severity == "high" for item in rule_suggestions):
        total_score = min(total_score, 79.0)

    if total_score >= 90:
        summary = "系列课结构较完整，主题、对象、递进和落地路径都比较稳，可以继续细化成交付稿。"
    elif total_score >= PASS_THRESHOLD:
        summary = "系列课基本成立，但仍有一些需要补强的结构或边界问题，处理后更适合通过。"
    else:
        summary = "系列课当前不建议直接通过，主要风险集中在主线发散、实战闭环不足或安全边界不清。"

    report = replace(base_report, total_score=total_score, suggestions=merged_suggestions[:5], summary=summary)
    return await _llm_refine_series_review(markdown_text, report, deepseek)
