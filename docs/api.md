# API Contract

## Envelope

```json
{
  "success": true,
  "request_id": "string",
  "thread_id": "string",
  "data": {},
  "error": null,
  "meta": {}
}
```

## POST /api/v1/threads

创建线程。

## POST /api/v1/threads/{thread_id}/messages

请求体：

```json
{
  "content": "我要做一节关于提示词设计的企业内训课",
  "user_id": "default-user"
}
```

## GET /api/v1/threads/{thread_id}/stream

SSE 事件：

- `assistant_message`
- `token_stream`
- `node_update`
- `review_batch`
- `artifact_updated`
- `audit_event`
- `file_uploaded`

## POST /api/v1/threads/{thread_id}/review-batches/{batch_id}/submit

请求体：

```json
{
  "submitter_id": "default-user",
  "review_actions": [
    {
      "suggestion_id": "s1",
      "action": "edit",
      "edited_suggestion": "把案例 2 的目标改成更具体的业务动作",
      "reviewer_id": "default-user",
      "comment": ""
    }
  ]
}
```
