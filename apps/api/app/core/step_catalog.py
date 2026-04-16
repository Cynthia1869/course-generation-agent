from __future__ import annotations

from dataclasses import dataclass

from app.core.schemas import CourseMode, RequirementSlot, StepStatus, WorkflowStage, WorkflowStepState


@dataclass(frozen=True)
class SlotDefinition:
    slot_id: str
    label: str
    prompt_hint: str
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepBlueprint:
    step_id: str
    label: str
    stage: WorkflowStage
    required_slots: tuple[str, ...]
    optional_slots: tuple[str, ...] = ()
    forbidden_topics: tuple[str, ...] = ()
    prerequisite_step_ids: tuple[str, ...] = ()
    needs_review: bool = True
    artifact_filename: str = ""
    generation_goal: str = ""
    prompt_id: str | None = None

    def to_state(self) -> WorkflowStepState:
        return WorkflowStepState(
            step_id=self.step_id,
            label=self.label,
            stage=self.stage,
            required_slots=list(self.required_slots),
            optional_slots=list(self.optional_slots),
            forbidden_topics=list(self.forbidden_topics),
            needs_review=self.needs_review,
        )


SLOT_DEFINITIONS: dict[str, SlotDefinition] = {
    "subject": SlotDefinition("subject", "学科", "这门课属于哪个学科，比如数学、物理、英语。", (r"(数学|物理|英语|语文|化学|生物|历史|地理)",)),
    "grade_level": SlotDefinition("grade_level", "年级", "面向哪个年级，比如初一、初二、初三、高一。", (r"(初一|初二|初三|高一|高二|高三|七年级|八年级|九年级)",)),
    "topic": SlotDefinition("topic", "知识点主题", "这节课具体讲哪个知识点，比如三角函数、一次函数、几何证明。", (r"主题是([^，。,]+)", r"课题是([^，。,]+)", r"课程主题是([^，。,]+)")),
    "audience": SlotDefinition("audience", "目标学员", "这门课主要给谁学，比如初中生、高中生、零基础学员。", (r"学员是([^，。,]+)", r"对象是([^，。,]+)", r"人群是([^，。,]+)", r"(初中生|高中生|小学生|零基础学员)")),
    "objective": SlotDefinition("objective", "课程目标", "学完后学员要能做成什么事，最好是一个可以验证的结果。", (r"目标是([^，。,]+)", r"希望达到([^，。,]+)")),
    "duration": SlotDefinition("duration", "课程时长", "总时长是多少，比如 30 分钟、90 分钟、2 小时。", (r"时长(?:是)?([^，。,]+)", r"课时(?:是)?([^，。,]+)")),
    "constraints": SlotDefinition("constraints", "限制与要求", "是否要基于真实案例、指定工具、商业场景、口播风格等。", (r"限制(?:是)?([^，。,]+)", r"约束(?:是)?([^，。,]+)", r"要求(?:是)?([^，。,]+)")),
    "course_positioning": SlotDefinition("course_positioning", "课程定位", "这一步更像什么课，比如入门课、提分课、训练营、实操带练。", (r"(入门课|进阶课|训练营|实操带练|提分课)",)),
    "target_problem": SlotDefinition("target_problem", "学员问题", "学员最想解决的具体问题是什么，最好一句话说清。", (r"解决([^，。,]+)", r"问题是([^，。,]+)")),
    "expected_result": SlotDefinition("expected_result", "学习结果", "学完后要交付什么结果，比如能独立做出什么、拿到什么。", (r"结果是([^，。,]+)", r"学完能([^，。,]+)")),
    "tone_style": SlotDefinition("tone_style", "课程风格", "希望课程风格偏实操、偏讲解、偏口语化还是偏严谨。", (r"(实操|讲解|口语化|严谨|案例驱动)",)),
    "course_goal": SlotDefinition("course_goal", "课程目标", "这一步的课程目标是什么，比如提分、建立框架、完成任务。", (r"课程目标是([^，。,]+)", r"目标是([^，。,]+)")),
    "module_design": SlotDefinition("module_design", "模块设计", "这一步准备拆成哪些模块，每个模块做什么。", (r"模块(?:设计)?是([^。；;\n]+)",)),
    "module_order": SlotDefinition("module_order", "模块顺序", "这些模块的先后顺序怎么安排。", (r"顺序(?:是)?([^。；;\n]+)",)),
    "teaching_strategy": SlotDefinition("teaching_strategy", "教学展开", "这一步希望先讲什么、再讲什么，用什么教学展开方式。", (r"教学(?:展开|方式)?是([^。；;\n]+)",)),
    "case_preferences": SlotDefinition("case_preferences", "案例要求", "案例希望偏什么场景、什么难度、需要避免什么类型。", (r"案例(?:要求|偏好)?是([^。；;\n]+)",)),
    "case_variable": SlotDefinition("case_variable", "关键变量", "案例里最关键的变量是什么，改了它结果会怎样。", (r"变量(?:是)?([^。；;\n]+)",)),
    "case_flow": SlotDefinition("case_flow", "案例流程", "案例步骤大概要怎么走，先做什么再做什么。", (r"流程(?:是)?([^。；;\n]+)",)),
    "failure_points": SlotDefinition("failure_points", "失败点", "案例里最容易失败或踩坑的点是什么。", (r"失败点(?:是)?([^。；;\n]+)", r"踩坑点(?:是)?([^。；;\n]+)")),
    "application_scene": SlotDefinition("application_scene", "应用场景", "案例主要发生在什么应用场景里。", (r"场景(?:是)?([^。；;\n]+)",)),
    "script_requirements": SlotDefinition("script_requirements", "逐字稿要求", "逐字稿有什么特殊要求，比如口语化、节奏快、强调互动。", (r"逐字稿(?:要求)?是([^。；;\n]+)",)),
    "resource_requirements": SlotDefinition("resource_requirements", "资源需求", "素材清单里需要哪些资源，比如讲义、练习、演示文件、讲师提示。", (r"素材(?:清单)?(?:要求|范围)?是([^。；;\n]+)",)),
    "configuration_requirements": SlotDefinition("configuration_requirements", "配置需求", "素材包或配置上需要提前准备什么环境、账号、工具、参数。", (r"配置(?:要求|需求)?是([^。；;\n]+)",)),
}


SERIES_STEP = StepBlueprint(
    step_id="series_framework",
    label="系列课程框架",
    stage=WorkflowStage.CONTENT,
    required_slots=("course_positioning", "topic", "audience", "target_problem", "expected_result"),
    optional_slots=("tone_style", "duration", "constraints"),
    forbidden_topics=("case_details", "script_content", "material_checklist"),
    artifact_filename="series_framework.md",
    generation_goal="生成系列课程框架",
    prompt_id="generate.series_framework",
)


SINGLE_STEPS: tuple[StepBlueprint, ...] = (
    StepBlueprint(
        step_id="course_title",
        label="课程标题",
        stage=WorkflowStage.CONTENT,
        required_slots=("topic", "audience", "target_problem", "expected_result", "tone_style"),
        optional_slots=("subject", "grade_level", "course_positioning", "constraints"),
        forbidden_topics=("case_details", "script_content", "material_checklist"),
        artifact_filename="course_title.md",
        generation_goal="生成课程标题",
        prompt_id="generate.course_title",
    ),
    StepBlueprint(
        step_id="course_framework",
        label="课程框架",
        stage=WorkflowStage.CONTENT,
        required_slots=("course_goal", "module_design", "module_order", "teaching_strategy"),
        optional_slots=("duration", "constraints"),
        forbidden_topics=("script_content",),
        prerequisite_step_ids=("course_title",),
        artifact_filename="course_framework.md",
        generation_goal="生成课程框架",
        prompt_id="generate.course_framework",
    ),
    StepBlueprint(
        step_id="case_output",
        label="案例输出",
        stage=WorkflowStage.CONTENT,
        required_slots=("case_preferences", "case_variable", "case_flow", "failure_points", "application_scene"),
        optional_slots=("constraints",),
        forbidden_topics=("script_content",),
        prerequisite_step_ids=("course_title", "course_framework"),
        artifact_filename="course_cases.md",
        generation_goal="生成案例输出",
        prompt_id="generate.case_output",
    ),
    StepBlueprint(
        step_id="script_output",
        label="逐字稿",
        stage=WorkflowStage.CONTENT,
        required_slots=("script_requirements",),
        optional_slots=("tone_style",),
        forbidden_topics=(),
        prerequisite_step_ids=("course_title", "course_framework", "case_output"),
        artifact_filename="course_script.md",
        generation_goal="生成逐字稿",
        prompt_id="generate.script_output",
    ),
    StepBlueprint(
        step_id="material_checklist",
        label="素材清单",
        stage=WorkflowStage.CONTENT,
        required_slots=("configuration_requirements", "resource_requirements"),
        optional_slots=(),
        forbidden_topics=(),
        prerequisite_step_ids=("course_title", "course_framework", "case_output", "script_output"),
        artifact_filename="material_checklist.md",
        generation_goal="生成素材清单",
        prompt_id="generate.material_checklist",
    ),
)


STEP_CATALOG: dict[str, StepBlueprint] = {SERIES_STEP.step_id: SERIES_STEP, **{step.step_id: step for step in SINGLE_STEPS}}
MODE_STEP_IDS: dict[CourseMode, tuple[str, ...]] = {
    CourseMode.SERIES: (SERIES_STEP.step_id,),
    CourseMode.SINGLE: tuple(step.step_id for step in SINGLE_STEPS),
}


def build_workflow_steps(mode: CourseMode) -> list[WorkflowStepState]:
    steps = [STEP_CATALOG[step_id].to_state() for step_id in MODE_STEP_IDS[mode]]
    if steps:
        steps[0].status = StepStatus.ACTIVE
    return steps


def get_step_blueprint(step_id: str) -> StepBlueprint:
    return STEP_CATALOG[step_id]


def get_slot_definition(slot_id: str) -> RequirementSlot:
    slot = SLOT_DEFINITIONS[slot_id]
    return RequirementSlot(slot_id=slot.slot_id, label=slot.label, prompt_hint=slot.prompt_hint)
