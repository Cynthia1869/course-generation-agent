请你作为课程内容评审员，严格按 rubric 评分，并返回 JSON。

JSON 结构:
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

阈值是 {threshold}。只返回 JSON。

Rubric:
{rubric_text}

Markdown:
{markdown}
