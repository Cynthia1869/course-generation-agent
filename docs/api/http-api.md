# HTTP API

## Threads

- `POST /api/v1/threads`
- `GET /api/v1/threads/{thread_id}`
- `POST /api/v1/threads/{thread_id}/messages`

## Files

- `POST /api/v1/threads/{thread_id}/files`
- `GET /api/v1/threads/{thread_id}/files`
- `GET /api/v1/threads/{thread_id}/artifacts/latest`
- `GET /api/v1/threads/{thread_id}/artifacts/{version}/diff/{prev_version}`

## Reviews

- `GET /api/v1/threads/{thread_id}/review-batches/{batch_id}`
- `POST /api/v1/threads/{thread_id}/review-batches/{batch_id}/submit`

## Events

- `GET /api/v1/threads/{thread_id}/stream`
- `GET /api/v1/threads/{thread_id}/events`
