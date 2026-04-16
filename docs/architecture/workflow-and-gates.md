# 工作流与 Gate 行为

## 适用范围

本文档描述系统的 workflow 行为、gate 规则、接口约束、模型分工和 prompt 组织方式。本文档只定义现行规则。

## 工作流主链路

当前主链路由线程消息进入 LangGraph workflow 后推进，核心顺序如下：

1. `intake_message`
2. `requirement_gap_check`
3. 分流到 `clarify_question` / `confirm_requirements` / `decision_update`
4. `source_parse`
5. `generate_step_artifact`
6. `critique_score`
7. `auto_improve` 或 `human_review_interrupt`
8. `approved_feedback_merge`
9. `revise_step_artifact`
10. `completion_gate`

线程存在可修订版本且用户通过接口或审核动作提交修订意图时，会进入再生成路径。

## Gate 定义

### `clarification gate`

触发条件：

- 当前 step 的必填 requirement slot 存在缺失值

必须行为：

- 系统只追问一个缺失项
- 问题范围必须限制在当前 step 所需信息
- 系统状态保持在 `collecting_requirements`
- 不进入生成

结果：

- 线程新增一条 assistant 澄清消息
- 发布 `clarification_started` 与 `clarification_completed` 事件
- 写入时间线事件

### `confirm gate`

触发条件：

- 当前 step 的必填 requirement slot 已满足
- 最新用户消息不属于确认类回复

必须行为：

- 系统输出当前 step 摘要
- 系统明确声明本 step 只生成当前 step 内容，不提前展开后续内容
- 系统等待用户显式确认后才进入生成

确认识别：

- 确认回复通过确认语句模式识别，例如 `开始生成`、`没问题`、`确认`、`继续下一步`

结果：

- 状态保持在 `collecting_requirements`
- 写入 `REQUIREMENTS_READY_FOR_CONFIRMATION`

### `review gate`

触发条件：

- 当前 step 已生成 `artifact`

必须行为：

- 系统必须调用自动评审
- 评审结果必须形成 `review batch`
- 若得分低于阈值且自动优化次数未达上限，则进入自动修订
- 若得分未达阈值，则当前 step 不能被确认

结果：

- 发布 `review_batch` 与 `review_ready`
- 写入 review 时间线事件

### `upload asset` 边界

触发条件：

- 用户调用 `/threads/{thread_id}/files` 上传文件

边界规则：

- 上传文件只进入 `source_manifest` 与 `saved_artifacts`
- `context` 类上传作为生成上下文输入
- `package` 类上传作为素材包记录
- 上传行为不会自动确认当前 step
- 上传行为不会自动触发生成完成

结果：

- 记录 `FILE_UPLOADED` 与 `FILE_PARSED`
- 发布 `file_uploaded`

## 步骤确认行为

接口：

- `POST /api/v1/threads/{thread_id}/confirm-step`

确认必须满足以下条件：

- 请求中的 `step_id` 等于当前活动 step
- 当前 step 状态为 `active`
- 当前 step 已存在生成产物
- 当前 step 如果要求评审，则最新评审必须存在
- 当前 step 如果要求评审，则最新评审分数必须达到阈值

确认成功后的结果：

- 当前 step 状态改为 `completed`
- 当前 step 的 `confirmed version` 被记录
- 若存在下一 step，则下一 step 成为活动 step
- 若不存在下一 step，则线程状态为 `completed`

## 接口边界

### 主事实来源

- 机器契约：[/Users/c14h14n3/Desktop/course-generation-agent/docs/api/openapi.json](docs/api/openapi.json)
- 人类总览：[/Users/c14h14n3/Desktop/course-generation-agent/docs/api/http-api.md](docs/api/http-api.md)

### 线程控制接口

- `PATCH /threads/{thread_id}/mode` 重置工作流步骤到目标 `course mode`
- `POST /threads/{thread_id}/confirm-step` 只负责确认当前 step
- `POST /threads/{thread_id}/messages` 负责输入新消息
- `PUT /threads/{thread_id}/messages/last` 与 `DELETE /threads/{thread_id}/messages/last` 只修改最近一条用户消息

### 版本接口

- `GET /artifacts/latest` 获取当前 `generated version`
- `PATCH /artifacts/latest` 人工编辑当前 `generated version`，并产生新版本
- `GET /artifacts/{version}` 获取指定版本
- `GET /artifacts/{version}/diff/{prev_version}` 获取版本差异
- `POST /regenerate` 基于指定或当前版本生成新版本，并重新进入评审

## 模型分工

### 运行配置

- 模型 provider：DeepSeek
- 配置入口：`config/llm.yaml`
- 运行中使用两个 profile：
  - `chat`
  - `review`

### `chat` profile 负责

- requirement extraction
- clarification
- step generation
- revision / regenerate

### `review` profile 负责

- step 级评审打分
- 结构化评审建议输出

### 模型命名约束

- 运行配置使用 `chat profile` 与 `review profile`
- 能力分类使用 `chat model` 与 `reasoning model`
- profile 名称与模型类型名称不混写

## Prompt 管理方式

### 管理规则

- 所有 prompt 通过 `prompt_id` 从 `prompts/prompt_catalog.yaml` 解析
- `prompt_id` 与 `step_id` 一起定义 prompt 的归属和用途
- step 生成必须使用 step blueprint 中声明的 prompt
- review 与 improve prompt 以 step artifact 为对象，而不是以整线程为对象

### 现有组织方式

- `clarify.<step_id>`
- `generate.<step_id>`
- `review.step_artifact`
- `improve.step_artifact`
- `extract.requirements`

## 测试关注点

- 缺失必填信息时是否稳定命中 `clarification gate`
- 必填信息齐全但未确认时是否稳定命中 `confirm gate`
- 生成后是否必定创建 `review batch`
- 低分时是否阻止确认当前 step
- 上传文件后是否只进入上下文，不直接完成 step
