# Overview

制课生成 Agent 0.1.0 是一个面向企业内训/知识库课程的生成系统。用户在网页中通过真实对话补全需求，并可上传 PDF、DOCX、Markdown、TXT 资料。系统生成 Markdown 主稿，内容包含单课框架、案例设计和逐字稿。随后小模型按固定 rubric 评分并提出建议，人工逐条通过、编辑或驳回，系统只根据人工确认后的意见继续改稿，直到评分达到 8 分且人工通过。

# Why This Architecture

系统采用 `LangGraph + LangChain + FastAPI + Vue`：

- `LangGraph` 负责业务状态机、持久化恢复、interrupt 与 streaming。
- `LangChain` 负责 OpenAI 兼容模型层、消息抽象和结构化输出。
- `FastAPI` 负责 HTTP、SSE、文件上传和服务编排。
- `Vue` 负责真实对话 + 稿件 + 审核三栏页面。

# Frontend UX

- 中间画布：首页态是居中的输入框和上传入口。
- 左侧页签：
  - `对话`
  - `当前稿`
  - `对比`
- 右侧抽屉：
  - 总分
  - 分项得分
  - 逐条建议
  - 人工动作

# Backend Architecture

- `api`: 接口层
- `application`: 服务编排
- `graph`: LangGraph 图与节点
- `models`: 模型网关
- `review`: rubric 与评分
- `documents`: 文件解析
- `persistence`: 存储适配
- `audit`: 审计与日志

# Graph Workflow

1. `intake_message`
2. `requirement_gap_check`
3. `clarify_question`
4. `decision_update`
5. `source_parse`
6. `outline_generate`
7. `case_design_generate`
8. `script_generate`
9. `draft_assemble`
10. `critique_score`
11. `human_review_interrupt`
12. `approved_feedback_merge`
13. `revise_draft`
14. `completion_gate`

# State & JSON Contracts

图内使用统一 `ThreadState`，其核心字段：

- `messages`
- `requirement_slots`
- `decision_ledger`
- `decision_summary`
- `source_manifest`
- `draft_artifact`
- `review_batches`
- `approved_feedback`
- `version_chain`
- `run_metadata`

所有节点出入参必须是 JSON 可序列化对象，所有外部接口返回统一 envelope。

# APIs

- `POST /api/v1/threads`
- `GET /api/v1/threads/{thread_id}`
- `POST /api/v1/threads/{thread_id}/messages`
- `GET /api/v1/threads/{thread_id}/stream`
- `POST /api/v1/threads/{thread_id}/files`
- `GET /api/v1/threads/{thread_id}/files`
- `GET /api/v1/threads/{thread_id}/artifacts/latest`
- `GET /api/v1/threads/{thread_id}/artifacts/{version}/diff/{prev_version}`
- `GET /api/v1/threads/{thread_id}/review-batches/{batch_id}`
- `POST /api/v1/threads/{thread_id}/review-batches/{batch_id}/submit`
- `GET /api/v1/threads/{thread_id}/events`

# Logging & Audit

三层日志：

- `application_log`
- `graph_event_log`
- `audit_log`

统一字段：

- `timestamp`
- `level`
- `service`
- `env`
- `request_id`
- `thread_id`
- `run_id`
- `node_name`
- `event_type`
- `artifact_version`
- `model_provider`
- `model_name`
- `latency_ms`
- `status`
- `payload_summary`

# Streaming

SSE 推送事件：

- `assistant_message`
- `token_stream`
- `node_update`
- `review_batch`
- `artifact_updated`
- `audit_event`
- `file_uploaded`

# Repository & Environment

- 项目根目录提供 `.venv`
- 后端使用 `python3.12`
- 远程仓库通过 `gh` 管理
- Git 以 `main + feature/*` 为基础

# Testing

- 单元测试：slot、ledger、rubric、review merge
- 集成测试：对话 -> 生成 -> 评分 -> 人审 -> 改稿 -> 完成
- 接口测试：SSE、review submit、artifact diff
- 审计测试：事件完整性与线程回放
