# Prompt 管理

## 结论

当前 prompt 管理以 `prompts/prompt_catalog.yaml` 为唯一索引入口。运行时不在业务代码中硬编码长 prompt 文本，而是通过 `prompt_id` 解析文件、校验输入变量并完成渲染。

## 主事实

- prompt 根目录：`prompts/`
- catalog：`prompts/prompt_catalog.yaml`
- 运行时解析器：`app/core/prompt_registry.py`
- 当前 provider 目录：`prompts/deepseek/`

## 管理规则

### 唯一定位规则

- 每条 prompt 必须有唯一 `prompt_id`
- `prompt_id` 是代码调用 prompt 的唯一稳定标识
- 同一条 prompt 不允许在 catalog 中重复定义

### 归属规则

- `mode` 表示 prompt 适用的 `course mode`
- `step_id` 表示 prompt 归属的 step；如果为 `null`，表示该 prompt 不绑定某个单独 step
- `purpose` 用于区分 `extract / clarify / generate / review / improve`

### 输入规则

- catalog 中声明的 `input_vars` 是最小必填集
- `PromptRegistry.validate_inputs()` 会校验输入变量是否完整
- 缺少变量时，运行时应视为配置错误，而不是静默降级

## 当前 prompt 组织方式

### Requirement extraction

- `extract.requirements`

### Clarification

- `clarify.series_framework`
- `clarify.course_title`
- `clarify.course_framework`
- `clarify.case_output`
- `clarify.script_output`
- `clarify.material_checklist`

### Generation

- `generate.series_framework`
- `generate.course_title`
- `generate.course_framework`
- `generate.case_output`
- `generate.script_output`
- `generate.material_checklist`
- `generate.legacy_full_draft` 保留兼容用途

### Review and improve

- `review.step_artifact`
- `improve.step_artifact`

## 当前运行流程

1. 业务代码传入 `prompt_id`
2. `PromptRegistry` 从 catalog 解析 prompt 规格
3. registry 读取对应 Markdown 模板
4. registry 校验 `input_vars`
5. registry 渲染模板并交给模型客户端

## 与模型配置的关系

- prompt 不直接决定模型实例
- prompt 负责定义任务目标和输入变量
- 模型选择由 `config/llm.yaml` 中的 profile 决定
- 当前 `chat` profile 负责 extract、clarify、generate、improve
- 当前 `review` profile 负责 review

## 调试入口

- 查看 catalog：`prompts/prompt_catalog.yaml`
- 查看模板文件：`prompts/deepseek/**/*.md`
- 查看渲染器：`apps/api/app/core/prompt_registry.py`
- 查看模型调用方：`apps/api/app/llm/deepseek_client.py`

## 测试关注点

- 新增 prompt 时是否补充了 `prompt_id`、`mode`、`step_id`、`purpose`
- 业务代码引用的 `prompt_id` 是否存在于 catalog
- 运行时变量是否覆盖 catalog 的 `input_vars`
- 文档是否把非规范路径误写为标准路径
