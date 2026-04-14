# Decision Model

## Current Status

当前系统已经开始沉淀决策训练样本，但还没有自动学习。

人工审核提交后，会把以下信息沉淀为训练记录：

- 用户上下文
- 决策摘要
- 草稿片段
- 模型问题
- 模型建议
- 人工动作（approve / edit / reject）

## Export

- `GET /api/v1/decision-records`
- `GET /api/v1/threads/{thread_id}/decision-records`
- `GET /api/v1/decision-model/status`

也可以先把运行中的记录导出到本地 JSONL，再执行：

```bash
python scripts/decision_model/export_decision_dataset.py
```

## Train

当前训练脚手架使用 Hugging Face 官方 `transformers.Trainer`：

```bash
python scripts/decision_model/train_decision_model.py
```

基模型默认使用：

- `distilbert-base-multilingual-cased`

## Predict

```bash
echo '{"text":"..."}' | python scripts/decision_model/predict_decision.py
```

## Important Note

这只是“准备好决策模型训练链路”，不是自动学习。
如果要真正持续学习，还需要：

- 更稳定的训练样本沉淀
- 周期性评估
- 模型版本管理
- 人工验收

## Data Persistence

人工审核记录会自动追加到：

- `data/decision_model/decision_records.jsonl`

因此后端重启后，训练样本不会因为内存线程丢失而消失。
