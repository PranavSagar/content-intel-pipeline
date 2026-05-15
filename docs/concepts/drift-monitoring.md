# Drift Monitoring — Evidently

## What problem are we solving?

You trained a model. It got 94.64% accuracy. You deployed it.

Three months later, accuracy has quietly dropped to 78% — but nobody noticed
because nothing "broke." The API still returns 200 OK. The pipeline still
processes messages. The model still outputs labels. It's just... wrong more often.

This is model drift. It's one of the most common silent failures in production ML.

---

## What is drift?

### Plain English
Think of a spam filter trained in 2020. At that time, "COVID relief fund"
was a phrase that appeared in spam. By 2023, it appears in legitimate
government emails too. The model learned that phrase = spam, but the world
changed. The model didn't.

The model didn't break. The world drifted.

### Two types of drift

**Data drift (input drift)**
The distribution of incoming articles changes. For example:
- You trained on news from 2004 (AG News dataset)
- The pipeline receives articles from 2024
- Language patterns, topics, and vocabulary have shifted
- The model sees inputs it was never trained on

**Concept drift (output drift)**
The relationship between input and correct label changes. For example:
- "Apple" articles used to be Business news (Apple the company was smaller)
- Now "Apple" articles are evenly split between Business and Sci/Tech
- The model still classifies them as Business — but that's increasingly wrong

### What we monitor
For this project we monitor **label distribution drift**:
- Reference: what label distribution did we see during the AG News test set?
  (approximately 25% each for World, Sports, Business, Sci/Tech)
- Production: what label distribution are we seeing in the live pipeline?
- If production drifts significantly from reference, something changed

We also monitor **confidence score drift**:
- Reference: average confidence during the initial test run
- Production: if average confidence drops, the model is "less sure" — possibly
  seeing inputs far from its training distribution

---

## Why Evidently?

Evidently is an open-source Python library specifically built for ML monitoring.
It takes a reference dataset and a production dataset and generates a statistical
report comparing them.

### What it gives you
- **DataDriftPreset**: tests every column for distribution shift using
  statistical tests (Jensen-Shannon divergence for categories,
  Wasserstein distance for continuous values)
- **HTML reports**: visual, shareable — you can open them in a browser
- **JSON output**: machine-readable — can be logged to MLflow or sent to Grafana
- **No server required**: runs as a Python script, no extra infrastructure

### Alternatives we considered

| Tool | Notes |
|------|-------|
| **WhyLogs (WhyLabs)** | Good for streaming; requires WhyLabs cloud account for dashboards |
| **Arize AI** | Production-grade; paid for serious use, free tier limited |
| **Fiddler AI** | Enterprise, expensive |
| **Great Expectations** | Data quality validation, not drift detection |
| **Custom stats (scipy)** | KL divergence or chi-squared by hand — reinventing Evidently |
| **Evidently** | Open source, no server, HTML + JSON output, most common in portfolio projects |

We chose Evidently because it's zero-infrastructure: just a Python script that
reads from our SQLite database and writes an HTML report. No cloud account needed.

---

## What "reference" means

Evidently compares two datasets:
1. **Reference** — what "normal" looks like (your baseline)
2. **Current** — recent production data

For our project:
- **Reference**: the label distribution from the AG News test set
  (the same 7,600 articles our producer sends). Expected: ~25% each label.
- **Current**: whatever our consumer has classified in the last N hours/days

The reference is fixed — it's what the model was validated on.
If production drifts from it, something is worth investigating.

---

## What we built

```
classifications.db (SQLite)
        ↓
  src/monitoring/drift.py
        ↓
    Evidently
     ├── Label distribution drift report (HTML)
     ├── Confidence score drift report (HTML)
     └── Drift metrics → MLflow (so you can track drift over time)
```

The script:
1. Loads the reference distribution (expected label proportions from AG News)
2. Loads recent classifications from SQLite (last 24 hours by default)
3. Runs Evidently drift tests on label distribution and confidence scores
4. Saves an HTML report you can open in a browser
5. Logs a `drift_detected: true/false` metric to MLflow

---

## How to interpret the report

Evidently uses statistical tests to decide if drift is significant:

**For label distribution (categorical):**
Uses chi-squared test or Jensen-Shannon divergence. If the p-value is below
a threshold (default 0.05), drift is flagged.

Plain English: "The probability that this distribution shift happened by
random chance is less than 5%. Something real changed."

**For confidence scores (continuous):**
Uses Wasserstein distance (earth mover's distance). Measures how much
"work" it would take to transform one distribution into the other.

Plain English: "How far apart are these two distributions?" A score of 0
means identical. Higher means more different.

**What to do when drift is detected:**
1. Look at the report — which label drifted? All of them, or just one?
2. Pull a sample of the flagged articles — do they look different from training data?
3. If drift is real: retrain on newer data, or investigate data source changes
4. If it's a false alarm: adjust the detection threshold

---

## Trade-offs we accepted

**Label distribution as proxy for accuracy**
We don't have ground truth labels for production articles — we can't compute
actual accuracy. Label distribution is a proxy: if the model suddenly classifies
90% of articles as "Business", something is probably wrong even without labels.

In production you'd use human annotation of a sample (called "golden set")
to get real accuracy estimates periodically.

**SQLite not a time-series store**
We query SQLite with `WHERE created_at > ?` to get recent data. For high
volume (millions of classifications/day), you'd use a proper time-series
database (TimescaleDB, InfluxDB) or a data warehouse (BigQuery, Snowflake).
SQLite is fine for our 1 msg/sec demo rate.

**Batch monitoring, not streaming**
Our drift script runs periodically (every 24 hours via GitHub Actions).
Streaming drift detection (Kafka Streams, Flink) would catch drift within
minutes but requires significant infrastructure. Batch is the right tradeoff
for a portfolio project.

**HTML report not a live dashboard**
Evidently generates a static HTML file. To get a live dashboard you'd
push drift metrics to Prometheus → Grafana. We log to MLflow instead —
same history, less operational complexity.
