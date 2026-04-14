from __future__ import annotations

from pathlib import Path

from datasets import load_dataset
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    DataCollatorWithPadding,
    Trainer,
    TrainingArguments,
)


ROOT = Path(__file__).resolve().parents[2]
DATASET_PATH = ROOT / "data" / "decision_model" / "train.jsonl"
OUTPUT_DIR = ROOT / "data" / "decision_model" / "model"
BASE_MODEL = "distilbert-base-multilingual-cased"


def main() -> None:
    dataset = load_dataset("json", data_files=str(DATASET_PATH), split="train")
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)

    def preprocess(batch):
        return tokenizer(batch["text"], truncation=True, max_length=512)

    tokenized = dataset.map(preprocess, batched=True)
    model = AutoModelForSequenceClassification.from_pretrained(BASE_MODEL, num_labels=3)
    training_args = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        overwrite_output_dir=True,
        num_train_epochs=3,
        per_device_train_batch_size=4,
        learning_rate=3e-5,
        logging_steps=10,
        save_strategy="epoch",
        report_to=[],
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized,
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer=tokenizer),
    )
    trainer.train()
    trainer.save_model(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))


if __name__ == "__main__":
    main()
