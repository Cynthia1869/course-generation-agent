# 日志与审计

## 结论

当前系统的可观测性由三类信息组成：

- 审计事件：面向运维、追责与离线分析
- 时间线事件：面向产品和测试理解线程过程
- SSE 事件：面向前端实时更新

三者的对象不同，不应混写。

## Audit Event

### 用途

- 记录线程级行为事实
- 记录模型调用上下文与失败信息
- 为训练样本、回放与运维排查提供证据

### 关键字段

- `thread_id`
- `event_type`
- `artifact_version`
- `model_provider`
- `model_name`
- `status`
- `error_code`
- `payload_summary`

### 当前常见事件

- `THREAD_CREATED`
- `THREAD_DELETED`
- `MESSAGE_RECEIVED`
- `MESSAGE_REPLACED`
- `FILE_UPLOADED`
- `FILE_PARSED`
- `ARTIFACT_EDITED`
- `REVIEW_ACTION_SUBMITTED`
- `THREAD_PAUSED`
- `THREAD_RESUMED`
- `THREAD_FAILED`
- graph 节点保存时写入的流程事件，例如 `CLARIFICATION_REQUESTED`、`DECISION_CONFIRMED`、`DRAFT_GENERATED`、`REVIEW_BATCH_CREATED`、`DRAFT_REVISED`

## Timeline Event

### 用途

- 面向产品、测试和前端查看线程过程
- 只保留用户可理解的业务时间线

### 当前常见事件

- `thread_created`
- `mode_changed`
- `user_message`
- `message_retracted`
- `message_replaced`
- `clarification_completed`
- `requirements_confirmed`
- `generation_started`
- `generation_completed`
- `review_ready`
- `artifact_edited`
- `revision_started`
- `revision_completed`
- `review_submitted`
- `step_confirmed`

## SSE Event

### 用途

- 将 workflow 进度和对象更新实时推给前端

### 当前常见事件

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

## LangSmith

当前 tracing 按 LangChain / LangGraph 官方方式接入，不做自定义协议封装。启用条件如下：

- `LANGSMITH_TRACING=true`
- `LANGSMITH_API_KEY=...`
- `LANGSMITH_PROJECT=course-agent`
- `LANGSMITH_ENDPOINT=https://api.smith.langchain.com`

参考：

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)

## 排查顺序

1. 先看线程时间线，判断卡在需求、生成、评审还是确认
2. 再看 SSE 事件，确认前端是否收到状态变化
3. 再看审计事件，确认后端是否实际执行、失败在哪里
4. 如涉及模型输出异常，再结合 LangSmith trace 看 prompt 和响应

## 测试关注点

- 用户可见流程是否都能在时间线中定位
- 关键失败是否都写入审计事件
- 前端依赖的实时事件是否与后端真实事件名称一致
