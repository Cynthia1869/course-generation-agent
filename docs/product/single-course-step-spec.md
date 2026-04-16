# 单课步骤规范

## 适用范围

本文档只描述当前版本 `single` 模式下的 5 个 step。系列课只保留结构，不适用本文的完整验收规则。

## 全局规则

- 当前 step 未确认前，不进入下一 step
- 当前 step 的 `generated version` 必须先经过 `review gate`
- 只有活动 step 才允许调用确认接口
- 当前 step 确认后，系统清空当前 `draft_artifact`，并将焦点切到下一 step
- 上传资料可以补充上下文，但不会自动替代 step 确认

## Step 1: `course_title`

### 负责内容

- 生成单课标题方向
- 收敛课程主题、目标学员、目标问题、预期结果、风格等基础定位

### 必要输入

- `topic`
- `audience`
- `target_problem`
- `expected_result`
- `tone_style`

### 可选输入

- `subject`
- `grade_level`
- `course_positioning`
- `constraints`

### 不负责内容

- 不展开课程模块设计
- 不展开案例细节
- 不展开逐字稿
- 不展开素材清单

### 验收点

- 产物必须聚焦课程命名与定位
- 产物不得提前写出案例、逐字稿或素材清单正文

## Step 2: `course_framework`

### 负责内容

- 定义课程目标、模块设计、模块顺序与教学展开方式
- 以已确认的标题和定位为前置约束

### 必要输入

- `course_goal`
- `module_design`
- `module_order`
- `teaching_strategy`

### 可选输入

- `duration`
- `constraints`

### 不负责内容

- 不生成完整逐字稿
- 不把案例展开成详细口播文本

### 验收点

- 产物必须说明课程结构与教学展开
- 产物不得替代后续案例输出与逐字稿 step

## Step 3: `case_output`

### 负责内容

- 生成案例方案
- 明确案例偏好、关键变量、案例流程、失败点与应用场景

### 必要输入

- `case_preferences`
- `case_variable`
- `case_flow`
- `failure_points`
- `application_scene`

### 可选输入

- `constraints`

### 不负责内容

- 不写逐字稿正文
- 不重写课程框架

### 验收点

- 产物必须包含案例的场景、变量、流程与风险点
- 产物不得将案例说明直接扩展为完整授课口播

## Step 4: `script_output`

### 负责内容

- 生成逐字稿
- 基于已形成的标题、框架与案例输出组织授课文本

### 必要输入

- `script_requirements`

### 可选输入

- `tone_style`

### 不负责内容

- 不重新定义课程标题
- 不重新设计课程框架
- 不生成素材清单条目

### 验收点

- 产物必须是可直接用于讲授的逐字稿文本
- 产物必须继承前序 step 的约束与已确认内容

## Step 5: `material_checklist`

### 负责内容

- 输出课程交付所需的资源、配置与准备项
- 明确资源需求与配置需求

### 必要输入

- `configuration_requirements`
- `resource_requirements`

### 不负责内容

- 不改写逐字稿
- 不补写新的教学模块
- 不新增与当前课程无关的扩展功能说明

### 验收点

- 产物必须是可执行的素材与配置清单
- 产物必须服务于当前已确认课程内容，而不是生成新的课程正文

## 步骤确认规则

- 只能确认当前活动 step
- 当前 step 必须存在已生成的 `artifact`
- 当前 step 默认需要评审
- 若最新 `review batch` 不存在，确认必须被拒绝
- 若最新 `review batch.total_score < threshold`，确认必须被拒绝
- 确认成功后：
  - 当前 step 状态变为 `completed`
  - 当前 step 的 `confirmed version` 被写入
  - 下一 step 变为 `active`
  - 线程状态回到 `collecting_requirements`，等待下一 step 输入

## 测试关注点

- 每个 step 是否只产出本 step 负责内容
- 缺失必填信息时是否只追问一个缺失项
- 用户未确认时是否不会直接进入生成
- 低于评审阈值时是否无法确认 step
- 确认后是否清理当前 step 的草稿并切换到下一 step
