# HTTP API 总览

## Source Of Truth

字段、类型和错误模型以 OpenAPI 为准：

- 机器契约：`docs/api/openapi.json`
- 导出命令：`./.venv/bin/python scripts/export_openapi.py`
- 前端类型生成：`cd frontend && npm run generate:api`

本文档只保留路由边界、用途和测试关注点，不维护第二份字段级真相。

## 线程与工作流控制

| Method | Path | 用途 | 关键边界 |
|---|---|---|---|
| POST | `/api/v1/threads` | 创建线程 | 创建后按 `course mode` 初始化 step |
| GET | `/api/v1/threads` | 获取线程列表 | 返回线程摘要，不替代详情接口 |
| GET | `/api/v1/threads/{thread_id}` | 获取线程详情 | 返回线程摘要与当前状态 |
| PATCH | `/api/v1/threads/{thread_id}/mode` | 切换 `course mode` | 切换后重建步骤，并清空当前草稿与评审态 |
| POST | `/api/v1/threads/{thread_id}/messages` | 提交用户消息 | 只负责接收输入，不直接返回生成结果 |
| PUT | `/api/v1/threads/{thread_id}/messages/last` | 修改最后一条用户消息 | 只允许修改最近一条用户消息 |
| DELETE | `/api/v1/threads/{thread_id}/messages/last` | 撤回最后一条用户消息 | 只影响最近可回退的输入 |
| POST | `/api/v1/threads/{thread_id}/pause` | 暂停线程 | 只暂停当前线程执行态 |
| POST | `/api/v1/threads/{thread_id}/resume` | 恢复线程 | 恢复被暂停的线程 |
| POST | `/api/v1/threads/{thread_id}/confirm-step` | 确认当前 step | 只能确认活动 step，且必须通过评审阈值 |
| POST | `/api/v1/threads/{thread_id}/regenerate` | 基于历史或当前版本再生成 | 会生成新版本并重新评审 |
| DELETE | `/api/v1/threads/{thread_id}` | 删除线程 | 删除线程聚合及其关联记录 |

## 交付物与上传文件

| Method | Path | 用途 | 关键边界 |
|---|---|---|---|
| GET | `/api/v1/threads/{thread_id}/files` | 获取 `upload asset` 列表 | 只返回上传结果与解析状态 |
| POST | `/api/v1/threads/{thread_id}/files` | 上传 `upload asset` | `category` 当前支持 `context` 与 `package` |
| GET | `/api/v1/threads/{thread_id}/artifacts/latest` | 获取当前 `generated version` | 返回当前最新版本，不代表已确认 |
| PATCH | `/api/v1/threads/{thread_id}/artifacts/latest` | 人工编辑当前版本 | 会生成新的 `generated version` |
| GET | `/api/v1/threads/{thread_id}/artifacts/{version}` | 获取指定版本 | 用于查看历史版本 |
| GET | `/api/v1/threads/{thread_id}/artifacts/{version}/diff/{prev_version}` | 获取版本 diff | 用于验收修订变化 |
| GET | `/api/v1/threads/{thread_id}/versions` | 获取版本列表 | 版本列表用于回溯与选择基线 |

## 评审、决策与可观测性

| Method | Path | 用途 | 关键边界 |
|---|---|---|---|
| GET | `/api/v1/threads/{thread_id}/review-batches/{batch_id}` | 获取指定评审批次 | 评审对象为 step 级 artifact |
| POST | `/api/v1/threads/{thread_id}/review-batches/{batch_id}/submit` | 提交人工审核动作 | 动作为 `approve / edit / reject` |
| GET | `/api/v1/decision-records` | 导出全局决策记录 | 面向训练与审计，不面向普通用户流程 |
| GET | `/api/v1/threads/{thread_id}/decision-records` | 导出线程级决策记录 | 用于排查某线程的审核决策 |
| GET | `/api/v1/decision-model/status` | 查看决策模型状态 | 用于运维状态查看 |
| GET | `/api/v1/threads/{thread_id}/timeline` | 获取用户可读时间线 | 面向产品与测试排查 |
| GET | `/api/v1/threads/{thread_id}/history` | 获取 workflow checkpoint 历史 | 面向调试，不作为用户主视图 |
| GET | `/api/v1/threads/{thread_id}/stream` | 订阅 SSE 事件 | 用于流式更新前端状态 |
| GET | `/api/v1/threads/{thread_id}/events` | 获取事件流记录 | 面向排查事件回放 |

## 补充接口

| Method | Path | 用途 | 说明 |
|---|---|---|---|
| POST | `/api/v1/experiments/deepagents/plan` | 复杂规划 bundle | 补充能力 |
| POST | `/api/v1/experiments/deepagents/review` | 修订评审 bundle | 补充能力 |
| POST | `/api/v1/experiments/deepagents/research` | 案例研究 bundle | 补充能力 |

## SSE 事件

当前文档统一使用以下主称呼描述事件：

- `user_message`
- `assistant_message`
- `assistant_token`
- `assistant_stream_end`
- `clarification_started`
- `clarification_completed`
- `generation_started`
- `generation_chunk`
- `generation_completed`
- `artifact_updated`
- `review_batch`
- `review_ready`
- `revision_started`
- `revision_completed`
- `file_uploaded`
- `thread_paused`
- `thread_resumed`
- `thread_failed`
- `audit_event`
- `node_update`

## 测试关注点

- `confirm-step` 是否只允许确认活动 step
- `mode` 切换后是否清空不再适用的 step 状态
- 人工编辑 `artifacts/latest` 是否产生新版本
- `regenerate` 是否重新评审并发布 `review_ready`
- 上传 `upload asset` 是否不会直接替代 step 确认
