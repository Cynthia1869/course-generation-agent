# 调试入口

## 结论

定位问题时，先判断问题属于产品边界、工作流执行、模型输出还是文件与版本，再进入对应入口。不要先改 prompt，也不要先假设前端有问题。

## 入口地图

### 产品边界问题

适用问题：

- 当前版本到底开放了什么
- 某一步是不是越界生成了后续内容
- 系列课是不是当前已支持完整链路

先看：

- `docs/product/course-agent-overview.md`
- `docs/product/single-course-step-spec.md`
- `docs/product/terminology.md`

### 工作流与 gate 问题

适用问题：

- 为什么一直在追问
- 为什么没有进入生成
- 为什么评审过了还不能确认
- 为什么上传了资料却没有自动产出

先看：

- `docs/architecture/workflow-and-gates.md`
- `apps/api/app/workflows/course_graph.py`
- `apps/api/app/application/course_agent_use_cases.py`
- `apps/api/app/core/step_catalog.py`

### API 契约问题

适用问题：

- 前后端字段不一致
- 某个路由的返回体和文档不一致
- 生成类型与后端 schema 漂移

先看：

- `docs/api/openapi.json`
- `docs/api/http-api.md`
- `apps/api/app/api/routes/*.py`

## 模型与 Prompt 问题

适用问题：

- 当前到底用了哪个模型
- 为什么某一步拿错了 prompt
- catalog 修改后没有生效

先看：

- `config/llm.yaml`
- `prompts/prompt_catalog.yaml`
- `apps/api/app/core/prompt_registry.py`
- `apps/api/app/llm/deepseek_client.py`

## 文件、版本与评审问题

适用问题：

- 当前版本与确认版本不一致
- diff 看不到变化
- review batch 与 artifact 对不上

先看：

- `apps/api/app/storage/thread_store.py`
- `apps/api/app/storage/repositories.py`
- `apps/api/app/core/schemas.py`

重点对象：

- `StepArtifactRecord.current_version`
- `StepArtifactRecord.confirmed_version`
- `DraftArtifact.version`
- `ReviewBatch.draft_version`

## 日志与回放问题

适用问题：

- 前端没更新，但后端是否已经执行
- 线程在哪个节点失败
- checkpoint 历史是否能复现问题

先看：

- 审计事件
- 时间线事件
- SSE 事件
- `GET /api/v1/threads/{thread_id}/history`
- `GET /api/v1/threads/{thread_id}/timeline`

## 标准排查顺序

1. 查线程详情，确认 `course mode`、当前 `step`、线程状态
2. 查时间线，确认停在需求、生成、评审还是确认
3. 查最新 `artifact`、版本列表和 `review batch`
4. 查审计事件与 SSE 事件
5. 如涉及模型行为，再查 `prompt_id`、模型 profile 和 LangSmith trace

## 常见失败点

- step 输入未满足，导致一直卡在 `clarification gate`
- 用户未给确认语句，导致一直卡在 `confirm gate`
- 评审分数未达阈值，导致 `confirm-step` 被拒绝
- 上传文件已成功，但未重新触发生成，因此用户误以为系统未读取文件
- 历史 redesign 文档与当前规范混用，导致测试按旧规则验收
