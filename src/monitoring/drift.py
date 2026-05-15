import argparse
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
from dotenv import load_dotenv

# Evidently 0.7.x API: Report is in evidently root, presets moved to evidently.presets
from evidently import Report
from evidently.presets import DataDriftPreset

load_dotenv(dotenv_path=".env")

# AG News test set has exactly 1,900 examples per class (25% each).
# This is the distribution the model was validated on — our baseline.
REFERENCE_COUNTS = {"World": 1900, "Sports": 1900, "Business": 1900, "Sci/Tech": 1900}

# From our test run: model averaged ~0.97 confidence across the AG News test set.
REFERENCE_CONFIDENCE_MEAN = 0.97
REFERENCE_CONFIDENCE_STD = 0.05

MIN_SAMPLES = 30  # statistical tests are unreliable below this


def build_reference_df() -> pd.DataFrame:
    rng = np.random.default_rng(seed=42)
    rows = []
    for label, count in REFERENCE_COUNTS.items():
        confidences = np.clip(
            rng.normal(REFERENCE_CONFIDENCE_MEAN, REFERENCE_CONFIDENCE_STD, count),
            0.5,
            1.0,
        )
        for conf in confidences:
            rows.append({"label": label, "confidence": round(float(conf), 4)})
    return pd.DataFrame(rows)


def load_current_df(db_path: str, hours: int) -> pd.DataFrame:
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        """
        SELECT label, confidence, created_at
        FROM classifications
        WHERE created_at >= ? AND cached = 0
        ORDER BY created_at
        """,
        conn,
        params=(cutoff,),
    )
    conn.close()
    return df


def run_evidently(reference_df: pd.DataFrame, current_df: pd.DataFrame, html_path: str):
    # In evidently 0.7.x, Report.run() returns a Snapshot object.
    # The Snapshot holds results and has save_html() / dict() methods.
    snapshot = Report([DataDriftPreset()]).run(
        reference_data=reference_df[["label", "confidence"]],
        current_data=current_df[["label", "confidence"]],
    )
    snapshot.save_html(html_path)
    return snapshot.dict()


def extract_drift_result(report_dict: dict) -> tuple[bool, float, list[str]]:
    """
    Parse the Evidently 0.7 snapshot dict.
    DriftedColumnsCount gives the overall share of drifted columns.
    ValueDrift per column gives p-values — drift when p_value < threshold (0.05).
    """
    drifted_share = 0.0
    drifted_columns = []

    for metric in report_dict.get("metrics", []):
        name = metric.get("metric_name", "")
        value = metric.get("value")

        if "DriftedColumnsCount" in name and isinstance(value, dict):
            drifted_share = value.get("share", 0.0)

        if "ValueDrift" in name and isinstance(value, (int, float)):
            # value is the p-value; drift detected when p_value < threshold (0.05)
            config = metric.get("config", {})
            column = config.get("column", "")
            threshold = config.get("threshold", 0.05)
            if value < threshold:
                drifted_columns.append(column)

    # Dataset-level drift: triggered when ≥50% of columns drift (Evidently default)
    drift_detected = drifted_share >= 0.5
    return drift_detected, drifted_share, drifted_columns


def log_to_mlflow(
    drift_detected: bool,
    drift_share: float,
    drifted_columns: list[str],
    current_df: pd.DataFrame,
    html_path: str,
):
    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment("drift-monitoring")

    run_name = f"drift-{datetime.utcnow().strftime('%Y%m%d-%H%M')}"
    with mlflow.start_run(run_name=run_name):
        mlflow.log_metric("drift_detected", int(drift_detected))
        mlflow.log_metric("drift_share_of_columns", round(drift_share, 4))
        mlflow.log_metric("sample_count", len(current_df))
        mlflow.log_metric("mean_confidence", round(current_df["confidence"].mean(), 4))
        mlflow.log_metric("p10_confidence", round(current_df["confidence"].quantile(0.10), 4))

        label_dist = current_df["label"].value_counts(normalize=True)
        for label, share in label_dist.items():
            key = label.lower().replace("/", "_")
            mlflow.log_metric(f"label_share_{key}", round(share, 4))

        if drifted_columns:
            mlflow.set_tag("drifted_columns", ", ".join(drifted_columns))

        mlflow.log_artifact(html_path)

    print(f"[drift] metrics logged to MLflow run '{run_name}'")


def run(db_path: str = "classifications.db", hours: int = 24, output_dir: str = "reports"):
    Path(output_dir).mkdir(exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M")
    html_path = str(Path(output_dir) / f"drift_{timestamp}.html")

    print("[drift] building reference distribution (AG News test set — 25% per label)...")
    reference_df = build_reference_df()

    print(f"[drift] loading current data from '{db_path}' (last {hours}h, model-only rows)...")
    current_df = load_current_df(db_path, hours)

    if len(current_df) < MIN_SAMPLES:
        print(
            f"[drift] only {len(current_df)} rows in window "
            f"(need ≥ {MIN_SAMPLES}). Run the pipeline longer and retry."
        )
        return

    ref_dist = reference_df["label"].value_counts(normalize=True).round(3)
    cur_dist = current_df["label"].value_counts(normalize=True).round(3)
    print("\n  Label distribution comparison:")
    print(f"  {'Label':<12} {'Reference':>10} {'Current':>10}")
    for label in REFERENCE_COUNTS:
        print(f"  {label:<12} {ref_dist.get(label, 0):>10.1%} {cur_dist.get(label, 0):>10.1%}")
    print(
        f"\n  Current confidence — mean: {current_df['confidence'].mean():.3f}  "
        f"p10: {current_df['confidence'].quantile(0.1):.3f}\n"
    )

    print("[drift] running Evidently drift report...")
    report_dict = run_evidently(reference_df, current_df, html_path)
    print(f"[drift] report saved → {html_path}")

    drift_detected, drift_share, drifted_columns = extract_drift_result(report_dict)

    print("[drift] logging metrics to MLflow (DagsHub)...")
    log_to_mlflow(drift_detected, drift_share, drifted_columns, current_df, html_path)

    status = "DRIFT DETECTED" if drift_detected else "no drift detected"
    col_info = f"  drifted columns: {drifted_columns}" if drifted_columns else ""
    print(f"\n[drift] result: {status}{col_info}")
    print(f"[drift] open {html_path} in a browser for the full visual report")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run drift detection on classifications.db")
    parser.add_argument("--db", default="classifications.db", help="Path to SQLite DB")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours")
    parser.add_argument("--output-dir", default="reports", help="Where to save HTML reports")
    args = parser.parse_args()

    run(db_path=args.db, hours=args.hours, output_dir=args.output_dir)
