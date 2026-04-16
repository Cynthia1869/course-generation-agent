import os
import sys
from pathlib import Path

import pytest
import yaml

os.environ["APP_ENV"] = "test"
os.environ["DEEPSEEK_API_KEY"] = ""
API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.prompt_registry import PromptRegistry
from app.core.settings import get_settings
from app.llm.deepseek_client import DeepSeekClient, RequirementExtractionResult


class FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeRequirementExtractionResponse:
    def model_dump(self):
        return {"course_positioning": "入门课"}


class FakeStructured:
    def __init__(self, schema):
        self.schema = schema

    async def ainvoke(self, prompt: str):
        if self.schema is RequirementExtractionResult:
            return FakeRequirementExtractionResponse()
        return {"total_score": 8.5, "criteria": [], "suggestions": []}


class FakeModel:
    async def astream(self, prompt: str):
        yield FakeChunk("ok")

    async def ainvoke(self, prompt: str):
        return FakeChunk("ok")

    def with_structured_output(self, schema):
        return FakeStructured(schema)


@pytest.fixture
def prompt_registry():
    settings = get_settings()
    return PromptRegistry(settings.prompt_root_dir)


def test_prompt_registry_resolves_step_prompt_and_system_prompt(prompt_registry: PromptRegistry):
    spec = prompt_registry.resolve_prompt("generate.course_title")
    assert spec.mode == "single"
    assert spec.step_id == "course_title"
    assert spec.purpose == "generate"
    assert "structured_inputs" in spec.input_vars
    assert spec.system_prompt_id == "global.single_course_system"
    assert spec.file == "deepseek/generate/course_title.md"


def test_prompt_registry_resolves_global_prompt(prompt_registry: PromptRegistry):
    spec = prompt_registry.resolve_prompt("global.single_course_system")
    assert spec.purpose == "system"
    assert "allowed_input_layers" in spec.input_vars
    assert spec.file == "deepseek/system/single_course_system.md"


def test_prompt_registry_render_bundle_includes_global_prompt(prompt_registry: PromptRegistry):
    bundle = prompt_registry.render_bundle(
        "generate.course_title",
        step_label="课程标题",
        step_scope="只允许使用当前步骤结构化输入和上传资料摘要。",
        allowed_input_layers="当前步骤结构化输入、上传资料摘要",
        forbidden_input_layers="未来步骤内容、未确认产物、原始聊天消息",
        output_contract="只生成课程标题。",
        generation_goal="生成课程标题",
        structured_inputs="- 必填 | 主题: 三角函数",
        confirmed_artifacts="暂无已确认前序产物",
        source_summary="无上传资料",
    )
    assert bundle.prompt_ids == ("global.single_course_system", "generate.course_title")
    assert "单课模式的工作流执行模型" in bundle.system_prompt
    assert "你现在只生成“课程标题”" in bundle.user_prompt
    assert "禁止把以下内容当成本轮正式输入" in bundle.combined_prompt


def test_prompt_registry_missing_required_input_vars_raises(prompt_registry: PromptRegistry):
    with pytest.raises(ValueError) as exc:
        prompt_registry.render_bundle(
            "generate.course_title",
            step_label="课程标题",
            generation_goal="生成课程标题",
            structured_inputs="- 必填 | 主题: 三角函数",
            confirmed_artifacts="暂无已确认前序产物",
            source_summary="无上传资料",
        )
    assert "global.single_course_system" in str(exc.value)
    assert "step_scope" in str(exc.value)


def test_load_legacy_reads_current_catalog_file(prompt_registry: PromptRegistry):
    content = prompt_registry.load_legacy("deepseek/clarify/course_title.md")
    assert "当前唯一缺失的信息" in content


def test_prompt_registry_duplicate_prompt_id_raises(tmp_path: Path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "sample.md").write_text("hello {name}", encoding="utf-8")
    (prompts_dir / "prompt_catalog.yaml").write_text(
        yaml.safe_dump(
            {
                "prompts": [
                    {
                        "prompt_id": "dup.id",
                        "version": "v1",
                        "provider": "deepseek",
                        "mode": "single",
                        "step_id": "course_title",
                        "purpose": "generate",
                        "input_vars": ["name"],
                        "output_contract": "text",
                        "file": "sample.md",
                    },
                    {
                        "prompt_id": "dup.id",
                        "version": "v1",
                        "provider": "deepseek",
                        "mode": "single",
                        "step_id": "course_framework",
                        "purpose": "generate",
                        "input_vars": ["name"],
                        "output_contract": "text",
                        "file": "sample.md",
                    },
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate prompt_id"):
        PromptRegistry(prompts_dir)


def test_prompt_registry_missing_file_raises(tmp_path: Path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "prompt_catalog.yaml").write_text(
        yaml.safe_dump(
            {
                "prompts": [
                    {
                        "prompt_id": "missing.file",
                        "version": "v1",
                        "provider": "deepseek",
                        "mode": "single",
                        "step_id": "course_title",
                        "purpose": "generate",
                        "input_vars": ["name"],
                        "output_contract": "text",
                        "file": "missing.md",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError) as exc:
        PromptRegistry(prompts_dir)
    assert "missing.file" in str(exc.value)
    assert "missing.md" in str(exc.value)


@pytest.mark.asyncio
async def test_clarify_prompt_id_is_step_specific(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    client = DeepSeekClient(settings)
    captured: list[str] = []

    monkeypatch.setattr(client, "can_use_remote_llm", lambda: True)
    monkeypatch.setattr(client, "_build_chat_model", lambda profile: FakeModel())

    def fake_render_by_id(prompt_id: str, **kwargs):
        captured.append(prompt_id)
        return "prompt"

    monkeypatch.setattr(client.prompts, "render_by_id", fake_render_by_id)

    chunks = []
    async for chunk in client.stream_clarification(
        {
            "prompt_id": "clarify.course_title",
            "step_label": "课程标题",
            "step_scope": "只允许确认课程标题所需信息。",
            "allowed_input_layers": "当前步骤结构化输入、上传资料摘要",
            "forbidden_input_layers": "未来步骤内容、未确认产物、原始聊天消息",
            "output_contract": "只追问一个缺失字段。",
            "allowed_scope": "主题、对象、问题、结果、标题风格",
            "forbidden_scope": "案例、逐字稿、素材清单",
            "structured_inputs": "- 必填 | 主题: 三角函数",
            "missing_requirement": {"label": "标题风格", "prompt_hint": "希望偏实操还是偏讲解"},
        }
    ):
        chunks.append(chunk)

    assert captured == ["clarify.course_title"]
    assert chunks == ["ok"]


@pytest.mark.asyncio
async def test_generate_prompt_id_is_step_specific(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    client = DeepSeekClient(settings)
    captured: list[str] = []

    monkeypatch.setattr(client, "can_use_remote_llm", lambda: True)
    monkeypatch.setattr(client, "_build_chat_model", lambda profile: FakeModel())

    def fake_render_by_id(prompt_id: str, **kwargs):
        captured.append(prompt_id)
        return "prompt"

    monkeypatch.setattr(client.prompts, "render_by_id", fake_render_by_id)

    chunks = []
    async for chunk in client.stream_step_markdown(
        {
            "prompt_id": "generate.course_framework",
            "step_label": "课程框架",
            "step_scope": "只允许使用课程框架结构化输入和已确认标题。",
            "allowed_input_layers": "当前步骤结构化输入、已确认前序产物、上传资料摘要",
            "forbidden_input_layers": "未来步骤内容、未确认产物、原始聊天消息",
            "output_contract": "只生成课程框架。",
            "generation_goal": "生成课程框架",
            "structured_inputs": "- 必填 | 课程目标: 提分",
            "confirmed_artifacts": "## 课程标题 (v1)\n推荐标题",
            "source_summary": "无上传资料",
        }
    ):
        chunks.append(chunk)

    assert captured == ["generate.course_framework"]
    assert chunks == ["ok"]


@pytest.mark.asyncio
async def test_review_prompt_uses_step_specific_prompt_id(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    client = DeepSeekClient(settings)
    captured: list[str] = []

    monkeypatch.setattr(client, "can_use_remote_llm", lambda: True)
    monkeypatch.setattr(client, "_build_chat_model", lambda profile: FakeModel())

    def fake_render_by_id(prompt_id: str, **kwargs):
        captured.append(prompt_id)
        return "prompt"

    monkeypatch.setattr(client.prompts, "render_by_id", fake_render_by_id)

    await client.review_markdown(
        prompt_id="review.course_title",
        markdown="# Title",
        rubric=[],
        threshold=8.0,
        step_label="课程标题",
        step_scope="只评审课程标题",
        forbidden_topics="无",
    )
    assert captured == ["review.course_title"]


@pytest.mark.asyncio
async def test_improve_prompt_uses_step_specific_prompt_id(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    client = DeepSeekClient(settings)
    captured: list[str] = []

    monkeypatch.setattr(client, "can_use_remote_llm", lambda: True)
    monkeypatch.setattr(client, "_build_chat_model", lambda profile: FakeModel())

    def fake_render_by_id(prompt_id: str, **kwargs):
        captured.append(prompt_id)
        return "prompt"

    monkeypatch.setattr(client.prompts, "render_by_id", fake_render_by_id)

    await client.improve_markdown(
        prompt_id="improve.course_title",
        markdown="# Title",
        approved_changes=["补强标题理由"],
        structured_inputs="- 必填 | 主题: 三角函数",
        confirmed_artifacts="暂无已确认前序产物",
        source_summary="无上传资料",
        source_version=1,
        revision_goal="补强标题",
        step_label="课程标题",
        step_scope="只修订课程标题",
    )
    assert captured == ["improve.course_title"]
