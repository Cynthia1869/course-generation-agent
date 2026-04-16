# 统一术语表

本文档定义系统标准术语。产品文案、接口说明、测试描述和运维文档应使用同一主称呼，不混用近义词。

| 术语 | 主称呼 | 定义 | 不再建议混用 |
|---|---|---|---|
| mode | `course mode` | 线程采用的制课模式。取值为 `single` 或 `series`。 | 模式、单课模式/系列课模式（可作为解释，不替代主称呼） |
| step | `step` | 工作流中的一个可确认阶段。单课共有 5 个 step。 | 阶段、节点、环节 |
| clarification gate | `clarification gate` | 当 step 的必填槽位未满足时，系统先追问一个缺失项，且不进入生成。 | 澄清节点、追问节点 |
| confirm gate | `confirm gate` | 当 step 的必填槽位已满足但用户尚未明确确认时，系统先给出 step 摘要并等待确认回复。 | 确认节点、开始生成确认 |
| review gate | `review gate` | 当 step 已生成 `artifact` 后，系统先执行自动评审；若分数低于阈值则阻止确认。 | 评审节点、审核关卡 |
| artifact | `artifact` | 某次生成、修订或人工编辑后形成的 Markdown 版本对象。 | 草稿、稿件、产物版本 |
| confirmed version | `confirmed version` | step 被用户确认接受的版本，是 step 前进的正式依据。 | 已确认稿、最终稿 |
| generated version | `generated version` | step 最新一次生成、修订或人工编辑后形成的版本，未必已经确认。 | 当前草稿、最新稿 |
| upload asset | `upload asset` | 通过 `/files` 上传并进入系统边界的文件对象，按 `context` 或 `package` 分类。 | 上传文件、附件、素材包 |
| prompt_id | `prompt_id` | prompt catalog 中唯一定位一条 prompt 规范的标识符。 | prompt key、模板 ID |
| chat model | `chat model` | 用于对话生成类任务的模型类型称呼；系统中的 `chat` profile 用于生成、澄清、抽取和修订。 | 普通模型、生成模型 |
| reasoning model | `reasoning model` | 用于高推理密度任务的模型类型称呼；该术语仅用于能力分类，不作为系统运行 profile 名称。 | 思考模型（可在说明中出现，但不替代主称呼） |
## 模型命名规则

- 文档中的运行配置统一写为 `chat profile` 与 `review profile`
- 文档中的模型类型统一写为 `chat model` 与 `reasoning model`
- 产品与接口文案默认使用 profile 名称，不使用模型演进表述
