# Project Structure

## Root

- `apps/api/`: FastAPI + LangGraph + LangChain 后端
- `frontend/`: Vue 前端
- `config/`: 运行时配置文件
- `prompts/`: Prompt 模板
- `docs/`: 项目文档
- `requirements.txt`: Python 依赖

## API Service

- `app/api/routes/`: 按线程、文件、审核、事件拆分的路由
- `app/core/`: settings、schemas、prompt registry
- `app/files/`: 文件解析
- `app/llm/`: DeepSeek 调用
- `app/review/`: 评分规则
- `app/services/`: 服务编排
- `app/storage/`: 状态存储
- `app/workflows/`: LangGraph 工作流与节点

## Prompt Management

- `prompts/deepseek/clarify_requirements.md`
- `prompts/deepseek/generate_markdown.md`
- `prompts/deepseek/review_markdown.md`
- `prompts/deepseek/improve_markdown.md`
