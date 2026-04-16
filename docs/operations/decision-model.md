# 决策模型状态

## 当前状态

系统具备决策记录沉淀与导出链路。本文档描述现行的运维与调试事实。

## 已实现能力

- 人工审核动作会沉淀为 `DecisionRecord`
- 线程级和全局决策记录可通过 API 导出
- 本地 JSONL 数据可导出、训练和预测

## 边界说明

- 本文档覆盖离线数据沉淀、导出、训练和预测入口
- 本文档不定义在线模型切换与自动学习流程

## 数据来源

每条决策记录当前包含以下信息：

- 用户消息上下文
- 决策摘要
- 稿件摘录
- 模型问题
- 模型建议
- 人工动作

## 存储位置

- API 导出：
  - `GET /api/v1/decision-records`
  - `GET /api/v1/threads/{thread_id}/decision-records`
  - `GET /api/v1/decision-model/status`
- 本地数据：
  - `data/decision_model/decision_records.jsonl`

## 运维命令

### 导出数据集

```bash
python scripts/decision_model/export_decision_dataset.py
```

### 训练脚手架

```bash
python scripts/decision_model/train_decision_model.py
```

当前默认基模型：

- `distilbert-base-multilingual-cased`

### 预测验证

```bash
echo '{"text":"..."}' | python scripts/decision_model/predict_decision.py
```

## 运维边界

- 该链路属于离线运维能力
- 它不应被描述为当前生产生成链路的一部分
- 训练结果接入在线决策前，必须补齐评估、版本管理和回滚机制
