# 项目结构

## 目录分层

### 产品文档

- `docs/product/`
  - 当前产品开放范围
  - 统一术语
  - 单课 step 规范

### 技术行为文档

- `docs/architecture/`
  - 工作流与 gate 规则
  - 项目结构与实现入口
- `docs/api/`
  - OpenAPI 与 HTTP 路由总览

### 运维与调试文档

- `docs/operations/`
  - prompt 管理
  - 日志与审计
  - 决策模型状态
  - 调试入口

### 历史设计参考

- `docs/redesign/`
  - 历史方案与设计记录
  - 不再作为当前版本主事实

## 代码结构

### Root

- `apps/api/`: FastAPI + LangGraph + LangChain 后端
- `frontend/`: 当前唯一有效的 Vue 前端工程
- `apps/web/`: 预留目录，当前不承载生产前端代码
- `config/`: 运行时配置文件
- `prompts/`: prompt 模板
- `docs/`: 项目文档
- `apps/api/pyproject.toml`: Python 依赖与项目元数据权威入口
- `requirements.txt`: 兼容性安装清单

### API Service

- `app/api/routes/`: HTTP 路由入口
- `app/application/`: 应用层用例编排
- `app/core/`: schema、step catalog、prompt registry、settings
- `app/files/`: 文件解析
- `app/infrastructure/`: 实验与基础设施适配器
- `app/llm/`: 模型客户端
- `app/review/`: 评审 rubric
- `app/services/`: service facade
- `app/storage/`: SQLite 仓储与线程存储
- `app/workflows/`: LangGraph 工作流

## 契约与生成链路

- OpenAPI：`docs/api/openapi.json`
- 前端类型：`frontend/src/generated/api.d.ts`
- API 封装：`frontend/src/lib/api.ts`

## Prompt 与模型配置

- prompt catalog：`prompts/prompt_catalog.yaml`
- prompt 模板：`prompts/deepseek/**`
- 模型配置：`config/llm.yaml`
