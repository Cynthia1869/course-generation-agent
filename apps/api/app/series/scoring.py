from __future__ import annotations

import re
from dataclasses import dataclass

from app.series.schema import CourseFramework, LessonOutline

PRACTICE_KEYWORDS = {"实战", "实践", "案例", "应用", "演练", "训练", "项目", "落地", "复盘", "工作流"}
FOUNDATION_KEYWORDS = {"基础", "入门", "理解", "认知", "定位", "框架", "概念", "准备"}
ADVANCED_KEYWORDS = {"策略", "优化", "进阶", "方案", "案例", "实战", "应用", "复盘", "闭环"}
SPECIFICITY_HINTS = {"提升", "完成", "建立", "优化", "设计", "掌握", "搭建", "独立", "系统", "转变", "应用"}
BOUNDARY_KEYWORDS = {"边界", "风险", "合规", "审批", "人工", "校验", "免责声明", "安全"}
LESSON_DETAIL_PREFIXES = ("内容", "本课内容", "课时内容", "内容概述", "学习内容", "重点内容", "案例", "实战", "目标", "产出")
FRAMEWORK_SECTION_MARKERS = ("课程框架", "课程安排", "课时安排", "模块设计", "章节安排")
FIELD_ALIASES = {
    "course_name": ("课程名称", "课程标题", "系列课名称", "标题"),
    "target_user": ("目标学员", "适合人群", "适用人群", "目标人群", "面向对象"),
    "learner_current_state": ("学员当前状态", "当前状态", "学员现状", "用户当前状态", "当前卡点"),
    "learner_expected_state": ("学员期望状态", "期望状态", "理想状态", "学习结果", "期望结果"),
    "mindset_shift": ("思维转换", "关键思维转换", "认知转变", "关键转变"),
    "core_problem": ("课程核心问题", "核心问题", "要解决的问题", "核心矛盾"),
    "application_scenario": ("课程应用场景", "应用场景", "适用场景", "使用场景", "落地场景"),
}


@dataclass(slots=True)
class SeriesCriterion:
    criterion_id: str
    name: str
    weight: float
    score: float
    max_score: float
    reason: str


@dataclass(slots=True)
class SeriesSuggestion:
    criterion_id: str
    problem: str
    suggestion: str
    evidence_span: str
    severity: str = "medium"


@dataclass(slots=True)
class SeriesReviewReport:
    total_score: float
    criteria: list[SeriesCriterion]
    suggestions: list[SeriesSuggestion]
    summary: str


def _contains_keyword(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _keyword_hits(text: str, keywords: set[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _clamp_score(value: int) -> int:
    return max(1, min(5, value))


def _lesson_text(lesson: LessonOutline) -> str:
    return f"{lesson.title} {lesson.summary}"


def _late_stage_lessons(lessons: list[LessonOutline]) -> list[LessonOutline]:
    return lessons[max(0, len(lessons) // 2) :]


def _joined_lessons_text(lessons: list[LessonOutline]) -> str:
    return " ".join(_lesson_text(lesson) for lesson in lessons)


def _normalize_line(line: str) -> str:
    cleaned = line.strip()
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"^\s*[-*+]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*>\s*", "", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "").replace("`", "")
    return cleaned.strip()


def _append_summary(current_lesson: LessonOutline | None, text: str) -> None:
    if current_lesson is None:
        return
    addition = text.strip("：: -")
    if not addition:
        return
    if current_lesson.summary:
        current_lesson.summary = f"{current_lesson.summary} {addition}".strip()
    else:
        current_lesson.summary = addition


def _parse_inline_value(line: str, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        matched = re.match(rf"^{re.escape(alias)}\s*[：:]\s*(.+)$", line)
        if matched:
            return matched.group(1).strip()
    return None


def _search_field_from_text(text: str, aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        matched = re.search(rf"{re.escape(alias)}\s*[：:]\s*([^\n]+)", text)
        if matched:
            return matched.group(1).strip()
    return None


def _infer_course_name(lines: list[str]) -> str | None:
    for line in lines:
        normalized = _normalize_line(line)
        if not normalized:
            continue
        if any(marker in normalized for marker in FRAMEWORK_SECTION_MARKERS):
            continue
        if any(normalized.startswith(alias) for aliases in FIELD_ALIASES.values() for alias in aliases):
            continue
        if len(normalized) >= 4:
            return normalized
    return None


def _to_lesson_number(raw: str, fallback: int) -> int:
    digits = re.sub(r"\D", "", raw)
    if digits:
        return int(digits)
    chinese_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    raw = raw.strip()
    if raw == "十":
        return 10
    if raw.startswith("十"):
        return 10 + chinese_map.get(raw[1:], 0)
    if raw.endswith("十"):
        return chinese_map.get(raw[0], fallback) * 10
    if "十" in raw:
        left, right = raw.split("十", 1)
        return chinese_map.get(left, 1) * 10 + chinese_map.get(right, 0)
    return chinese_map.get(raw, fallback)


def parse_framework_markdown(markdown_text: str) -> CourseFramework:
    raw_lines = [line.rstrip() for line in markdown_text.splitlines()]
    lines = [_normalize_line(line) for line in raw_lines if _normalize_line(line)]
    parsed: dict[str, str] = {}
    lessons: list[LessonOutline] = []
    current_lesson: LessonOutline | None = None
    lesson_patterns = [
        re.compile(r"^第?\s*([一二三四五六七八九十\d]+)\s*(?:课|讲|节|模块|单元)\s*[：:、.)）-]?\s*(.+)$"),
        re.compile(r"^([0-9]+)\s*[.、)）-]\s*(.+)$"),
    ]

    for line in lines:
        for field_name, aliases in FIELD_ALIASES.items():
            if field_name in parsed:
                continue
            inline = _parse_inline_value(line, aliases)
            if inline:
                parsed[field_name] = inline
                current_lesson = None
                break
        else:
            matched_lesson = None
            for pattern in lesson_patterns:
                matched_lesson = pattern.match(line)
                if matched_lesson:
                    break
            if matched_lesson:
                lesson_number = _to_lesson_number(matched_lesson.group(1), len(lessons) + 1)
                current_lesson = LessonOutline(lesson_number=lesson_number, title=matched_lesson.group(2).strip(), summary="")
                lessons.append(current_lesson)
                continue

            detail_match = re.match(rf"^(?:{'|'.join(re.escape(prefix) for prefix in LESSON_DETAIL_PREFIXES)})\s*[：:]\s*(.+)$", line)
            if detail_match:
                _append_summary(current_lesson, detail_match.group(1))
                continue

            if any(marker in line for marker in FRAMEWORK_SECTION_MARKERS):
                continue
            if current_lesson is not None:
                _append_summary(current_lesson, line)

    full_text = "\n".join(lines)
    for field_name, aliases in FIELD_ALIASES.items():
        if field_name not in parsed:
            fallback = _search_field_from_text(full_text, aliases)
            if fallback:
                parsed[field_name] = fallback

    if "course_name" not in parsed:
        inferred_course_name = _infer_course_name(raw_lines)
        if inferred_course_name:
            parsed["course_name"] = inferred_course_name

    if not lessons:
        prose_lessons = re.findall(r"(?:第\s*([一二三四五六七八九十\d]+)\s*(?:课|讲|节)|([0-9]+)[.、)])\s*([^\n]+)", full_text)
        for index, item in enumerate(prose_lessons, start=1):
            raw_number = item[0] or item[1]
            title = item[2].strip()
            if title:
                lessons.append(LessonOutline(lesson_number=_to_lesson_number(raw_number, index), title=title, summary=""))

    return CourseFramework(
        course_name=parsed.get("course_name", "未命名系列课"),
        target_user=parsed.get("target_user", "待补充"),
        learner_current_state=parsed.get("learner_current_state", "待补充"),
        learner_expected_state=parsed.get("learner_expected_state", "待补充"),
        mindset_shift=parsed.get("mindset_shift", "待补充"),
        core_problem=parsed.get("core_problem", "待补充"),
        application_scenario=parsed.get("application_scenario", "待补充"),
        lessons=lessons,
    )


def _criterion_from_score(name: str, score: int, reason: str, weight: float) -> SeriesCriterion:
    return SeriesCriterion(criterion_id=name, name=name, weight=weight, score=float(score * 20), max_score=100.0, reason=reason)


def score_framework_markdown(markdown_text: str) -> SeriesReviewReport:
    framework = parse_framework_markdown(markdown_text)
    lessons = framework.lessons
    joined_lessons = _joined_lessons_text(lessons)
    late_lessons = _late_stage_lessons(lessons)
    lesson_count = len(lessons)

    goal_text = " ".join([framework.course_name, framework.core_problem, framework.learner_expected_state])
    goal_score = 3 + int(len(framework.course_name) >= 8) + int(_keyword_hits(goal_text, SPECIFICITY_HINTS) >= 2) - int(len(framework.core_problem) < 12)
    audience_text = " ".join([framework.target_user, framework.learner_current_state])
    audience_score = 3 + int(any(token in audience_text for token in ["零基础", "有经验", "转型", "岗位", "团队", "经理", "顾问", "老师"])) + int(len(framework.learner_current_state) >= 24) - int(len(framework.target_user) < 8)
    logic_score = 3
    if lessons:
        first_text = _lesson_text(lessons[0])
        last_text = _lesson_text(lessons[-1])
        logic_score += int(_contains_keyword(first_text, FOUNDATION_KEYWORDS))
        logic_score += int(_contains_keyword(last_text, PRACTICE_KEYWORDS | ADVANCED_KEYWORDS))
        logic_score -= int([lesson.lesson_number for lesson in lessons] != list(range(1, lesson_count + 1)))
        logic_score -= int(any(_contains_keyword(_lesson_text(lesson), PRACTICE_KEYWORDS) for lesson in lessons[:2]) and not _contains_keyword(first_text, FOUNDATION_KEYWORDS))
    else:
        logic_score = 1
    mindset_score = 3 + int("从" in framework.mindset_shift and "到" in framework.mindset_shift) + int("转变" in framework.mindset_shift or len(framework.mindset_shift) >= 18) - int(len(framework.mindset_shift) < 10)
    scenario_score = 3 + int(len(framework.application_scenario) >= 12) + int(any(token in framework.application_scenario for token in ["场景", "流程", "客户", "项目", "协作", "运营", "交付"])) - int(len(framework.application_scenario) < 8)
    size_score = 3 + int(4 <= lesson_count <= 8) + int(5 <= lesson_count <= 7) - int(lesson_count <= 2 or lesson_count >= 12) - int(lesson_count > 0 and len(joined_lessons) / lesson_count < 18)
    practice_hits = _keyword_hits(joined_lessons, PRACTICE_KEYWORDS)
    late_practice_hits = sum(1 for lesson in late_lessons if _contains_keyword(_lesson_text(lesson), PRACTICE_KEYWORDS))
    practice_score = 2 + int(practice_hits >= 2) + int(late_practice_hits >= 1) + int(late_practice_hits >= 2 or "工作流" in joined_lessons)
    boundary_text = " ".join([framework.core_problem, framework.application_scenario, joined_lessons])
    boundary_score = 3 + int(_contains_keyword(boundary_text, BOUNDARY_KEYWORDS)) + int("审批" in boundary_text or "高风险" in boundary_text)

    criteria = [
        _criterion_from_score("目标清晰度", _clamp_score(goal_score), "课程名称、核心问题和期望结果越具体，目标越清晰。", 12.0),
        _criterion_from_score("目标学员明确度", _clamp_score(audience_score), "目标学员越具体，课程越容易收束。", 12.0),
        _criterion_from_score("内容逻辑性", _clamp_score(logic_score), "前半段铺认知、后半段做应用时，结构更稳。", 18.0),
        _criterion_from_score("思维转换明确度", _clamp_score(mindset_score), "思维转换最好写成明确的迁移。", 10.0),
        _criterion_from_score("应用场景清晰度", _clamp_score(scenario_score), "应用场景越具体，课程落地性越强。", 12.0),
        _criterion_from_score("课程规模合理性", _clamp_score(size_score), "课时规模和信息密度匹配时更像可交付产品。", 16.0),
        _criterion_from_score("实战性", _clamp_score(practice_score), "后半段是否有案例/项目/复盘是关键。", 14.0),
        _criterion_from_score("边界与约束清晰度", _clamp_score(boundary_score), "风险边界写清楚，交付更稳。", 6.0),
    ]
    total_score = round(sum(item.score * (item.weight / 100.0) for item in criteria), 2)

    suggestions: list[SeriesSuggestion] = []
    if late_practice_hits == 0:
        suggestions.append(SeriesSuggestion("实战性", "后半段缺少明显的案例、项目或复盘，系列课会更像知识讲解。", "把最后三分之一课时改成案例拆解、完整演练或项目复盘。", "课程框架", "high"))
    if lesson_count < 3:
        suggestions.append(SeriesSuggestion("课程规模合理性", "课时过少，难以支撑完整的系列课程递进。", "至少拆成 3 到 5 课，明确基础、方法、应用的递进。", "课程框架", "medium"))
    if len(framework.target_user) < 8:
        suggestions.append(SeriesSuggestion("目标学员明确度", "目标学员描述过宽，后续每节课会容易失焦。", "把目标学员写成“谁、现在卡在哪里、想拿到什么结果”的格式。", "目标学员", "medium"))
    if len(framework.core_problem) < 12:
        suggestions.append(SeriesSuggestion("目标清晰度", "课程核心问题不够聚焦，用户难以快速理解这套课到底解决什么。", "把课程核心问题改写成一个明确矛盾，并与课程名称、期望结果对齐。", "课程核心问题", "medium"))

    if total_score >= 85:
        summary = "课程框架整体达标，主线、对象和落地路径都比较清晰。"
    elif total_score >= 80:
        summary = "课程框架基本可通过，但还需要补强部分结构和落地细节。"
    else:
        summary = "课程框架还不稳，主要问题集中在逻辑递进、实战闭环或目标收束上。"

    return SeriesReviewReport(total_score=total_score, criteria=criteria, suggestions=suggestions[:5], summary=summary)
