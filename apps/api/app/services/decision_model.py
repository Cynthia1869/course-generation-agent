from __future__ import annotations

from pathlib import Path

from app.core.settings import Settings


class DecisionModelService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def status(self) -> dict:
        data_dir = self.settings.decision_model_data_dir
        records_path = data_dir / "decision_records.jsonl"
        train_path = data_dir / "train.jsonl"
        model_dir = data_dir / "model"
        record_count = 0
        if records_path.exists():
            record_count = sum(1 for _ in records_path.open("r", encoding="utf-8"))
        return {
            "data_dir": str(data_dir),
            "records_path": str(records_path),
            "train_path": str(train_path),
            "model_dir": str(model_dir),
            "record_count": record_count,
            "train_dataset_exists": train_path.exists(),
            "model_ready": (model_dir / "config.json").exists(),
        }
