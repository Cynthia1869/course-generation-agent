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

from app.core.schemas import LLMProviderConfig
from app.core.settings import get_settings
from app.llm.deepseek_client import DeepSeekClient, ProviderProfiles, RequirementExtractionResult


class FakeChunk:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeRequirementExtractionResponse:
    def model_dump(self):
        return {"topic": "三角函数"}


class FakeStructured:
    def __init__(self, model_name: str, schema, sink: list[tuple[str, object]]) -> None:
        self.model_name = model_name
        self.schema = schema
        self.sink = sink

    async def ainvoke(self, prompt: str):
        self.sink.append((self.model_name, self.schema))
        if self.schema is RequirementExtractionResult:
            return FakeRequirementExtractionResponse()
        return {"total_score": 8.6, "criteria": [], "suggestions": []}


class FakeModel:
    def __init__(self, model_name: str, sink: list[tuple[str, object]]) -> None:
        self.model_name = model_name
        self.sink = sink

    async def astream(self, prompt: str):
        self.sink.append((self.model_name, "astream"))
        yield FakeChunk("ok")

    async def ainvoke(self, prompt: str):
        self.sink.append((self.model_name, "ainvoke"))
        return FakeChunk("ok")

    def with_structured_output(self, schema):
        return FakeStructured(self.model_name, schema, self.sink)


@pytest.fixture(autouse=True)
def isolate_settings_env(tmp_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_deepseek_client_reads_profiles_from_llm_config(tmp_path: Path):
    settings = get_settings()
    config_path = tmp_path / "llm.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "default_provider": "deepseek",
                "providers": {
                    "deepseek": {
                        "api_base_env": "DEEPSEEK_BASE_URL",
                        "api_key_env": "DEEPSEEK_API_KEY",
                        "base_url": "https://chat.example.com",
                    },
                    "deepseek_reasoner": {
                        "api_base_env": "DEEPSEEK_REASONER_BASE_URL",
                        "api_key_env": "DEEPSEEK_REASONER_API_KEY",
                        "base_url": "https://reasoner.example.com",
                    },
                },
                "profiles": {
                    "chat": {
                        "provider": "deepseek",
                        "model": "deepseek-chat-v3",
                        "temperature": 0.45,
                    },
                    "review": {
                        "provider": "deepseek_reasoner",
                        "model": "deepseek-reasoner",
                        "temperature": 0.05,
                    },
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    client = DeepSeekClient(settings.model_copy(update={"llm_config_file": config_path}))

    assert client.profile.chat.provider == "deepseek"
    assert client.profile.chat.model == "deepseek-chat-v3"
    assert client.profile.chat.base_url == "https://chat.example.com"
    assert client.profile.clarify.model == "deepseek-chat-v3"
    assert client.profile.extract.model == "deepseek-chat-v3"
    assert client.profile.generate.model == "deepseek-reasoner"
    assert client.profile.review.provider == "deepseek_reasoner"
    assert client.profile.review.model == "deepseek-reasoner"
    assert client.profile.review.base_url == "https://reasoner.example.com"
    assert client.profile.improve.model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_deepseek_methods_route_to_expected_profiles(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    client = DeepSeekClient(settings)
    call_sink: list[tuple[str, object]] = []
    built_profiles: list[LLMProviderConfig] = []

    client.profile = ProviderProfiles(
        chat=LLMProviderConfig(provider="chat-provider", model="chat-model", temperature=0.4),
        clarify=LLMProviderConfig(provider="clarify-provider", model="clarify-model", temperature=0.2),
        extract=LLMProviderConfig(provider="extract-provider", model="extract-model", temperature=0.0),
        generate=LLMProviderConfig(provider="generate-provider", model="generate-model", temperature=0.4),
        review=LLMProviderConfig(provider="review-provider", model="review-model", temperature=0.1),
        improve=LLMProviderConfig(provider="improve-provider", model="improve-model", temperature=0.3),
    )

    monkeypatch.setattr(client, "can_use_remote_llm", lambda: True)

    def fake_build_chat_model(profile: LLMProviderConfig):
        built_profiles.append(profile)
        return FakeModel(profile.model, call_sink)

    monkeypatch.setattr(client, "_build_chat_model", fake_build_chat_model)

    await client.ask_clarification(
        {
            "prompt_id": "clarify.course_title",
            "step_label": "课程标题",
            "step_scope": "只允许确认课程标题所需信息。",
            "allowed_input_layers": "当前步骤结构化输入、上传资料摘要",
            "forbidden_input_layers": "未来步骤内容、未确认产物、原始聊天消息",
            "output_contract": "只追问一个缺失字段。",
            "structured_inputs": "- 必填 | 主题: 三角函数",
            "missing_requirement": {"label": "标题风格", "prompt_hint": "偏实操还是偏讲解"},
        }
    )
    await client.extract_requirements(
        latest_user_message="我要做一门三角函数课",
        known_requirements={"audience": "初中生"},
        requirement_defs=[{"slot_id": "topic", "label": "主题", "prompt_hint": "知识点主题"}],
    )
    chunks = []
    async for chunk in client.stream_step_markdown(
        {
            "prompt_id": "generate.course_title",
            "step_label": "课程标题",
            "step_scope": "只生成课程标题",
            "allowed_input_layers": "当前步骤结构化输入、已确认前序产物、上传资料摘要",
            "forbidden_input_layers": "未来步骤内容、未确认产物、原始聊天消息",
            "output_contract": "只生成课程标题。",
            "generation_goal": "生成课程标题",
            "structured_inputs": "- 必填 | 主题: 三角函数",
            "confirmed_artifacts": "暂无已确认前序产物",
            "source_summary": "无上传资料",
        }
    ):
        chunks.append(chunk)
    review = await client.review_markdown(
        prompt_id="review.course_title",
        markdown="# 标题",
        rubric=[],
        threshold=8.0,
        step_label="课程标题",
        step_scope="只评审课程标题",
        forbidden_topics="无",
    )
    improved = await client.improve_markdown(
        prompt_id="improve.course_title",
        markdown="# 标题",
        approved_changes=["补强标题说明"],
        structured_inputs="- 必填 | 主题: 三角函数",
        confirmed_artifacts="暂无已确认前序产物",
        source_summary="无上传资料",
        source_version=1,
        revision_goal="补强标题",
        step_label="课程标题",
        step_scope="只修订课程标题",
    )

    assert [profile.model for profile in built_profiles] == [
        "clarify-model",
        "extract-model",
        "generate-model",
        "review-model",
        "improve-model",
    ]
    assert built_profiles[0].temperature == 0.2
    assert built_profiles[1].temperature == 0.0
    assert built_profiles[2].temperature == 0.4
    assert built_profiles[3].temperature == 0.1
    assert built_profiles[4].temperature == 0.3
    assert chunks == ["ok"]
    assert review["total_score"] == 8.6
    assert improved == "ok"
