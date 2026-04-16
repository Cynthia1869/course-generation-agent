请你只评审“{step_label}”这一个步骤的当前产物。

当前步骤禁止提前展开的话题：
{forbidden_topics}

阈值：
{threshold}

Rubric：
{rubric_text}

当前 Markdown：
{markdown}

返回要求：
1. 只返回 JSON
2. 只根据当前步骤要求评分
3. 不要把未来步骤缺失当成当前步骤缺陷
4. JSON 结构必须是：
{{
  "total_score": 0,
  "criteria": [
    {{
      "criterion_id": "",
      "name": "",
      "weight": 0,
      "score": 0,
      "max_score": 10,
      "reason": ""
    }}
  ],
  "suggestions": [
    {{
      "criterion_id": "",
      "problem": "",
      "suggestion": "",
      "evidence_span": "",
      "severity": "low|medium|high"
    }}
  ]
}}
