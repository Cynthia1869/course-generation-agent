# Logging & Audit Design

## 日志层次

### application_log

记录接口进入、异常、耗时和系统级行为。

### graph_event_log

记录 LangGraph 节点开始、结束、interrupt、resume 和 checkpoint 相关事件。

### audit_log

记录人工动作、版本完成、线程完成等责任边界事件。

## 审计事件

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

## 设计原则

- 正文默认只记录摘要
- 完整 prompt / response 不进入普通日志
- 人工编辑建议与原始建议并存
- 每个事件都能通过 `thread_id` 串联回放
