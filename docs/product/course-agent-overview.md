# 产品总览

## 结论

本系统是一个面向单课生成的 step-based 制课系统。系统按步骤推进，每一步都经过需求澄清、生成、评审和人工确认后才能进入下一步。系列课能力保留基础结构，但不作为主路径。

## 开放能力

### 已开放

- `single` 模式的完整 5 步工作流
- 每一步的缺失信息追问
- 每一步生成独立 `artifact`
- 每一步生成后的自动评审
- 人工审核提交与再修订
- 步骤确认后进入下一步
- 文件上传、解析、版本查看、版本 diff、人工编辑最新版本

### 保留能力

- `series` 模式
- `series_framework` 单步骨架

### 非主路径能力

- 系列课多步骤完整链路
- 新产品模式或新步骤类型
- 前端视觉体系重构

## 系统做什么

- 将用户对课程的需求逐步收敛为可确认的步骤输入
- 在当前步骤边界内生成对应交付物
- 对生成结果执行自动评审并产出结构化建议
- 将人工确认结果沉淀为版本、时间线与决策记录

## 系统不做什么

- 不在当前步骤提前展开后续步骤内容
- 不将上传文件自动视为已确认结论
- 不在评审通过后自动跨步推进；每个 step 都要求显式确认
- 不把系列课写成当前已完整上线能力

## 角色阅读入口

### 开发

- 先读：[/Users/c14h14n3/Desktop/course-generation-agent/docs/product/terminology.md](docs/product/terminology.md)
- 再读：[/Users/c14h14n3/Desktop/course-generation-agent/docs/product/single-course-step-spec.md](docs/product/single-course-step-spec.md)
- 补充：[/Users/c14h14n3/Desktop/course-generation-agent/docs/architecture/workflow-and-gates.md](docs/architecture/workflow-and-gates.md)

开发需要回答的问题：
- 当前支持哪些产品路径
- 每一步的输入边界和禁止项是什么
- 哪些行为由 gate 保证，哪些行为由接口或人工动作触发

### 测试

- 先读：[/Users/c14h14n3/Desktop/course-generation-agent/docs/product/single-course-step-spec.md](docs/product/single-course-step-spec.md)
- 再读：[/Users/c14h14n3/Desktop/course-generation-agent/docs/architecture/workflow-and-gates.md](docs/architecture/workflow-and-gates.md)
- 补充：[/Users/c14h14n3/Desktop/course-generation-agent/docs/operations/debugging-guide.md](docs/operations/debugging-guide.md)

测试需要回答的问题：
- 每一步应该生成什么，不应该生成什么
- 哪些条件会触发澄清、确认、评审、阻断
- 失败时应该看哪些日志、事件和版本记录

### 产品

- 先读：本页
- 再读：[/Users/c14h14n3/Desktop/course-generation-agent/docs/product/single-course-step-spec.md](docs/product/single-course-step-spec.md)

产品需要回答的问题：
- 已开放什么
- 未开放什么
- 每一步完成后对用户形成什么可见结果
