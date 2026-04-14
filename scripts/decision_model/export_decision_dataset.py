from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = ROOT / "data" / "decision_model" / "decision_records.jsonl"
TRAIN_PATH = ROOT / "data" / "decision_model" / "train.jsonl"

LABEL_MAP = {"approve": 0, "edit": 1, "reject": 2}


def build_text(record: dict) -> str:
    return (
        f"用户上下文:\n{record.get('user_message_context', '')}\n\n"
        f"决策摘要:\n{record.get('decision_summary', '')}\n\n"
        f"草稿片段:\n{record.get('draft_excerpt', '')}\n\n"
        f"模型问题:\n{record.get('model_problem', '')}\n\n"
        f"模型建议:\n{record.get('model_suggestion', '')}\n"
    ).strip()


def main() -> None:
    TRAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if RAW_PATH.exists():
        for line in RAW_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            action = record.get("human_action")
            if action not in LABEL_MAP:
                continue
            rows.append(
                {
                    "text": build_text(record),
                    "label": LABEL_MAP[action],
                    "label_name": action,
                }
            )
    with TRAIN_PATH.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"exported {len(rows)} records -> {TRAIN_PATH}")


if __name__ == "__main__":
    main()
