import os
import sys
from pathlib import Path

import pytest

os.environ["APP_ENV"] = "test"
os.environ["DEEPSEEK_API_KEY"] = ""
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.api.deps import get_service
from app.core.settings import get_settings
from app.series.decision_scoring import score_series_framework_markdown
from app.series.scoring import parse_framework_markdown


@pytest.fixture(autouse=True)
def isolate_test_state(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_settings.cache_clear()
    get_service.cache_clear()
    yield
    get_settings.cache_clear()
    get_service.cache_clear()


def test_parse_framework_markdown_accepts_loose_markdown_format():
    markdown = """
## AI 产品经理需求分析系列课

- 目标学员: 已经会写 PRD，但不会系统用 AI 提升需求分析效率的产品经理
- 当前状态：对 AI 有零散了解，但还没有稳定接入日常需求分析工作流
- 期望状态：能把 AI 用进需求洞察、PRD 结构化和复盘优化流程
- 关键思维转换：从把 AI 当问答工具，到把 AI 当产品工作流助手
- 核心问题：如何让产品经理把 AI 真正用进需求分析与 PRD 输出流程
- 使用场景：日常需求分析、方案拆解、PRD 产出和跨团队协作场景

### 课程安排
1. 认识 AI 产品工作流
内容：明确 AI 在需求分析流程中的角色定位和边界。

2. 用 AI 做需求洞察
围绕用户反馈、访谈纪要和数据线索完成问题整理。

3. 用 AI 提升 PRD 输出效率
本课内容：把需求分析结果转成结构更完整、表达更清晰的 PRD 初稿。
"""

    framework = parse_framework_markdown(markdown)

    assert framework.course_name == "AI 产品经理需求分析系列课"
    assert framework.target_user.startswith("已经会写 PRD")
    assert framework.learner_current_state.startswith("对 AI 有零散了解")
    assert framework.learner_expected_state.startswith("能把 AI 用进需求洞察")
    assert framework.application_scenario.startswith("日常需求分析")
    assert len(framework.lessons) == 3
    assert framework.lessons[1].title == "用 AI 做需求洞察"
    assert "访谈纪要" in framework.lessons[1].summary


@pytest.mark.asyncio
async def test_score_series_framework_markdown_flags_high_risk_without_boundary():
    service = get_service()
    markdown = """
课程名称：法务合同初审 AI 系列课
目标学员：想用 AI 提升法务合同初审效率的法务专员
学员当前状态：会做基础合同审阅，但缺少系统方法
学员期望状态：能独立用 AI 完成合同初审判断
思维转换：从人工逐条检查转变为先用 AI 给出初审结论
课程核心问题：如何让法务专员把 AI 真正用进合同初审与风险判断流程
课程应用场景：法务合同初审、风险判断和审批前预判场景

第1课：认识合同初审流程
内容：理解合同初审的基础动作。

第2课：AI 提升审阅效率
内容：学习如何让 AI 快速识别合同风险。

第3课：案例演练
内容：围绕典型合同案例完成完整演练。
"""

    report = await score_series_framework_markdown(markdown, service.graph.deepseek)

    assert report.total_score < 80
    assert any("人工复核" in item.suggestion or "使用限制" in item.suggestion for item in report.suggestions)


@pytest.mark.asyncio
async def test_score_series_framework_markdown_detects_core_workflow_gap():
    service = get_service()
    markdown = """
课程名称：AI 漫剧创作系列课
目标学员：想做 AI 漫剧副业的人
学员当前状态：会写一些简单脚本，但不会把漫剧做成完整成片
学员期望状态：能独立完成一个 AI 漫剧作品
思维转换：从会写脚本转变为会做完整作品
课程核心问题：如何独立完成一个 AI 漫剧作品
课程应用场景：副业创作和短视频内容输出场景

第1课：漫剧选题
内容：明确适合做漫剧的题材。

第2课：角色与分镜
内容：完成人物设定和分镜拆解。

第3课：案例拆解
内容：围绕一个漫剧案例拆解创作过程。
"""

    report = await score_series_framework_markdown(markdown, service.graph.deepseek)

    assert report.total_score < 80
    assert any("核心工作流" in item.criterion_id or "成片环节" in item.problem for item in report.suggestions)
