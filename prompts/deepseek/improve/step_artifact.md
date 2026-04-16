请只修订“{step_label}”这一个步骤的 Markdown 产物，并且只返回修订后的完整 Markdown。

当前步骤结构化输入：
{structured_inputs}

已确认前序产物：
{confirmed_artifacts}

上传资料摘要：
{source_summary}

基础版本：
v{source_version}

本轮修订目标：
{revision_goal}

必须执行的改进要求：
{approved_changes}

当前 Markdown：
{markdown}

硬约束：
1. 只能修订当前步骤
2. 不得改写前序已确认步骤内容
3. 不得提前补写未来步骤
4. 不得输出推理过程，只返回修订后的 Markdown
