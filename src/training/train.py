import os
import yaml
import numpy as np
import mlflow
import mlflow.pytorch
from pathlib import Path
from dotenv import load_dotenv
from transformers import (
    DistilBertForSequenceClassification,
    TrainingArguments,
    Trainer,
    EarlyStoppingCallback,
)
from sklearn.metrics import accuracy_score, f1_score

from src.training.dataset import load_ag_news, ID2LABEL, LABEL2ID, NUM_LABELS

load_dotenv()


# ── Evaluation ────────────────────────────────────────────────────────────────
# Called by Trainer after every epoch with all predictions on the test set.
# Returns a dict — every key becomes a metric column in MLflow.
def compute_metrics(eval_pred):
    logits, labels = eval_pred
    predictions = np.argmax(logits, axis=-1)
    return {
        "accuracy": accuracy_score(labels, predictions),
        "f1":       f1_score(labels, predictions, average="weighted"),
    }


# ── Training ──────────────────────────────────────────────────────────────────
def train(config: dict, subset: int = None):
    # Point MLflow at DagsHub — credentials come from .env
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(config["mlflow"]["experiment_name"])

    # Load and tokenize the dataset
    print("Loading dataset...")
    dataset, tokenizer = load_ag_news(
        tokenizer_name=config["model"]["base"],
        max_length=config["model"]["max_length"],
    )

    # subset is only used for quick smoke-tests — never for real training
    if subset:
        dataset["train"] = dataset["train"].select(range(subset))
        dataset["test"]  = dataset["test"].select(range(subset // 10))
        print(f"Subset mode: using {subset} train, {subset // 10} test examples")

    # Load DistilBERT with a fresh 4-class classification head on top
    print("Loading model...")
    model = DistilBertForSequenceClassification.from_pretrained(
        config["model"]["base"],
        num_labels=NUM_LABELS,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )

    tc = config["training"]

    args = TrainingArguments(
        output_dir="artifacts/checkpoints",
        num_train_epochs=tc["epochs"],
        per_device_train_batch_size=tc["batch_size"],
        per_device_eval_batch_size=64,
        learning_rate=float(tc["learning_rate"]),
        warmup_steps=tc["warmup_steps"],
        weight_decay=tc["weight_decay"],
        eval_strategy="epoch",      # evaluate after every epoch
        save_strategy="epoch",      # save checkpoint after every epoch
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        greater_is_better=True,
        logging_steps=100,          # print loss every 100 steps
        report_to="none",           # we handle MLflow ourselves below
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["test"],
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(
            early_stopping_patience=tc["early_stopping_patience"]
        )],
    )

    # Everything inside this block is one MLflow "run"
    with mlflow.start_run():
        # Log every hyperparameter — these become searchable/comparable in DagsHub
        mlflow.log_params({
            "base_model":    config["model"]["base"],
            "max_length":    config["model"]["max_length"],
            "epochs":        tc["epochs"],
            "batch_size":    tc["batch_size"],
            "learning_rate": tc["learning_rate"],
            "warmup_steps":  tc["warmup_steps"],
            "weight_decay":  tc["weight_decay"],
            "subset":        subset or "full",
        })

        print("Training...")
        trainer.train()

        # Evaluate on the full test set with the best checkpoint
        print("Evaluating...")
        metrics = trainer.evaluate()
        print(metrics)

        # Log final metrics to MLflow
        mlflow.log_metrics({
            "accuracy": metrics["eval_accuracy"],
            "f1":       metrics["eval_f1"],
            "loss":     metrics["eval_loss"],
        })

        print(f"\nFinal accuracy : {metrics['eval_accuracy']:.4f}")
        print(f"Final F1       : {metrics['eval_f1']:.4f}")


if __name__ == "__main__":
    config_path = Path(__file__).parent.parent.parent / "configs" / "training_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    train(config, subset=None)
