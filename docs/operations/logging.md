# Logging & Audit

## Event Types

- `THREAD_CREATED`
- `MESSAGE_RECEIVED`
- `CLARIFICATION_REQUESTED`
- `DECISION_CONFIRMED`
- `FILE_UPLOADED`
- `FILE_PARSED`
- `DRAFT_GENERATED`
- `REVIEW_BATCH_CREATED`
- `REVIEW_INTERRUPTED`
- `REVIEW_ACTION_SUBMITTED`
- `DRAFT_REVISED`
- `THREAD_COMPLETED`
- `BACKGROUND_TASK_FAILED`

## Stream Events

- `assistant_message`
- `token_stream`
- `node_update`
- `review_batch`
- `artifact_updated`
- `audit_event`
- `file_uploaded`

## LangSmith

按官方接法，设置以下环境变量即可启用 LangSmith tracing：

- `LANGSMITH_TRACING=true`
- `LANGSMITH_API_KEY=...`
- `LANGSMITH_PROJECT=course-agent`
- `LANGSMITH_ENDPOINT=https://api.smith.langchain.com`

当前系统不做自定义 Smith API 封装，直接走 LangChain / LangGraph 官方 tracing 机制。
