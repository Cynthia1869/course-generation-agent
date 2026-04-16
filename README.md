# 制课生成 Agent

一个基于 `LangGraph + LangChain + FastAPI + Vue` 的 step-based 制课系统。系统围绕单课 5 个步骤完成需求澄清、生成、评审、确认与交付物沉淀。

## 产品范围

- 主路径：`single` 模式
- 当前单课步骤：
  1. `course_title`：课程标题
  2. `course_framework`：课程框架
  3. `case_output`：案例输出
  4. `script_output`：逐字稿
  5. `material_checklist`：素材清单
- `series` 模式保留 `series_framework` 基础骨架
- 后端已具备：
  - `clarification gate`
  - `confirm gate`
  - `review gate`
  - `upload asset` 边界
- prompt 已按 `prompt_id` 与 `step_id` 管理
- 模型配置使用 DeepSeek；运行时存在 `chat` 与 `review` 两个模型 profile

## 文档导航

- 产品边界：[/Users/c14h14n3/Desktop/course-generation-agent/docs/product/course-agent-overview.md](docs/product/course-agent-overview.md)
- 统一术语：[/Users/c14h14n3/Desktop/course-generation-agent/docs/product/terminology.md](docs/product/terminology.md)
- 单课步骤规范：[/Users/c14h14n3/Desktop/course-generation-agent/docs/product/single-course-step-spec.md](docs/product/single-course-step-spec.md)
- 工作流与 gate：[/Users/c14h14n3/Desktop/course-generation-agent/docs/architecture/workflow-and-gates.md](docs/architecture/workflow-and-gates.md)
- API 总览：[/Users/c14h14n3/Desktop/course-generation-agent/docs/api/http-api.md](docs/api/http-api.md)
- Prompt 管理：[/Users/c14h14n3/Desktop/course-generation-agent/docs/operations/prompt-management.md](docs/operations/prompt-management.md)
- 日志与审计：[/Users/c14h14n3/Desktop/course-generation-agent/docs/operations/logging.md](docs/operations/logging.md)
- 调试入口：[/Users/c14h14n3/Desktop/course-generation-agent/docs/operations/debugging-guide.md](docs/operations/debugging-guide.md)

`docs/redesign/` 用于保留历史设计参考，不作为规范入口。

## 根目录结构

- `apps/api/`: FastAPI 后端与 LangGraph 工作流
- `frontend/`: 当前唯一有效的前端目录
- `apps/web/`: 预留目录，不作为运行入口
- `config/`: 模型与运行配置
- `prompts/`: prompt 模板与 catalog
- `docs/`: 产品、架构、接口、运维文档
- `apps/api/pyproject.toml`: Python 依赖与项目元数据权威入口
- `requirements.txt`: 兼容性安装清单

## 启动方式

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
# 或使用权威入口：
# pip install -e ./apps/api[dev]

cp .env.example .env
uvicorn app.main:app --app-dir apps/api --reload
```

```bash
cd frontend
npm install
npm run generate:api
npm run dev
```

## 工程约束

- OpenAPI 导出文件位于 `docs/api/openapi.json`
- 前端类型由 `frontend/src/generated/api.d.ts` 自动生成
- prompt 文件统一位于 `prompts/`
- `config/llm.yaml` 是当前模型配置入口
