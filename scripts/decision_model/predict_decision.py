from __future__ import annotations

import json
import sys
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "data" / "decision_model" / "model"
LABELS = {0: "approve", 1: "edit", 2: "reject"}


def main() -> None:
    payload = json.loads(sys.stdin.read())
    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    model = AutoModelForSequenceClassification.from_pretrained(str(MODEL_DIR))
    encoded = tokenizer(payload["text"], return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        logits = model(**encoded).logits
    label_id = int(torch.argmax(logits, dim=-1).item())
    print(json.dumps({"label": LABELS[label_id], "label_id": label_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
