# Build Log — Problems, Solutions, and Lessons

This document records every real problem hit while building this project —
what broke, why it broke, how it was fixed, and what the trade-off was.
Written as it happened, not reconstructed afterwards.

---

## What we built (layer by layer)

```
Layer 1: Fine-tuned DistilBERT on AG News → 94.64% accuracy
Layer 2: FastAPI serving with Prometheus metrics
Layer 3: Kafka pipeline (Redpanda Cloud) — producer + consumer
Layer 4: Redis cache (Upstash) + SQLite storage
Layer 5: Evidently drift monitoring → MLflow on DagsHub
```

Each layer added a real production concern:
- Layer 1: can we train a model that actually works?
- Layer 2: can we serve it reliably with observability?
- Layer 3: can we decouple the source from the classifier?
- Layer 4: can we avoid redundant work and persist results?
- Layer 5: can we detect when the model stops working well?

---

## Problem 1 — `ModuleNotFoundError: No module named 'dataset'`

**When:** Running `train.py` from the project root.

**What happened:**
```
ModuleNotFoundError: No module named 'dataset'
```

`train.py` had `from dataset import load_ag_news`. When run as a module
(`python -m src.training.train`), Python's module resolution looks for
`dataset` as a top-level package — not relative to `train.py`'s location.

**Why it happens:**
Running with `-m` changes the working directory context. Bare imports like
`from dataset import` work when you run the file directly from its own folder
(`python train.py`), but break when run as a module from the project root.

**Fix:**
Changed to absolute package imports:
```python
from src.training.dataset import load_ag_news, LABELS, ID2LABEL, LABEL2ID, NUM_LABELS
```
Also added `src/__init__.py` (empty file) to make `src` a proper Python package.
Without it, Python doesn't treat `src` as a package and the import still fails.

**Lesson:**
Always run training scripts as modules (`python -m src.training.train`) from
the project root, and always use absolute package imports. Relative imports
work locally but break the moment you add any CI pipeline or Docker container.

---

## Problem 2 — `TrainingArguments got unexpected keyword argument 'use_mps_device'`

**When:** Setting up training on Apple Silicon (M-series Mac).

**What happened:**
```
TypeError: TrainingArguments.__init__() got an unexpected keyword argument 'use_mps_device'
```

We passed `use_mps_device=True` to tell the Trainer to use the Mac GPU.

**Why it happens:**
`use_mps_device` was added as an explicit argument in older versions of
`transformers` because MPS (Metal Performance Shaders, Apple's GPU API)
wasn't auto-detected. In `transformers` 5.x, MPS is detected automatically
from the environment — the argument was removed.

**Fix:**
Removed `use_mps_device=True` from `TrainingArguments`. The Trainer
automatically selects MPS on Apple Silicon if PyTorch was installed with
MPS support (which it is by default on Mac).

**Lesson:**
Don't fight the framework. When a new version deprecates a manual override,
it's usually because the default behavior is now correct.

---

## Problem 3 — `ImportError: requires accelerate>=1.1.0`

**When:** First run of `train.py`.

**What happened:**
```
ImportError: Using the `Trainer` with `PyTorch` requires `accelerate>=1.1.0`
```

`transformers` 5.x requires the `accelerate` library to run the Trainer.
In older versions it was optional.

**Fix:**
```bash
pip install accelerate
```
Added to `requirements-training.txt`.

**Lesson:**
Lock your dependency versions in requirements files and install from them
at the start of a project. Finding missing dependencies mid-training run
is frustrating. In production you'd use a Dockerfile that installs everything
upfront, failing fast before wasting compute time.

---

## Problem 4 — `TypeError: '<=' not supported between instances of 'float' and 'str'`

**When:** Training started but crashed before the first epoch.

**What happened:**
```
TypeError: '<=' not supported between instances of 'float' and 'str'
```

The error was on the `learning_rate` comparison inside `TrainingArguments`.

**Why it happens:**
`configs/training_config.yaml` has:
```yaml
learning_rate: 2e-5
```

PyYAML parses `2e-5` (scientific notation) as a **string**, not a float.
So `tc["learning_rate"]` was the string `"2e-5"`, and passing it to
`TrainingArguments(learning_rate="2e-5")` caused the type error.

**Fix:**
```python
learning_rate=float(tc["learning_rate"])
```

**Why not just write `0.00002` in the YAML?**
Scientific notation is standard in ML papers and makes the number readable.
`2e-5` is immediately recognizable as a learning rate. `0.00002` requires
counting zeros. The explicit `float()` cast is worth the readability.

**Lesson:**
YAML has a type system that doesn't always do what you expect. Integers,
floats, booleans, and strings are inferred from syntax — and scientific
notation is not always treated as a float across YAML parsers. Always cast
explicitly when loading numeric config values that will be used in arithmetic.

---

## Problem 5 — Training took 20 hours instead of ~82 minutes

**When:** Left training running overnight.

**What happened:**
The full AG News training set (120,000 examples, 3 epochs) was expected to
take ~82 minutes on Apple Silicon MPS. It took over 20 hours.

**Why it happened:**
The Mac went to sleep. When macOS sleeps, the MPS GPU is throttled or
suspended. The Trainer kept running but on severely degraded compute.

**Fix:**
For future runs:
```bash
caffeinate -i python -m src.training.train
```
`caffeinate` prevents macOS from sleeping while the command runs.

**Lesson:**
Long-running training jobs need to be protected from the OS. On a laptop,
`caffeinate` (Mac), `systemd-inhibit` (Linux), or just a cloud GPU instance.
The cloud instance also eliminates the "my GPU is being used for a Teams call"
problem.

---

## Problem 6 — Upstash Kafka was deprecated mid-project

**When:** Setting up the Kafka broker.

**What happened:**
We planned to use Upstash for both Redis and Kafka (same provider, simpler
setup). When we went to create the Kafka cluster, the option wasn't there.
Upstash had deprecated their Kafka product and removed it from the dashboard.

**Why it happened:**
Upstash pivoted their product focus. Kafka is operationally complex to offer
as a managed service at low margins — they discontinued it.

**What we evaluated:**
| Option | Problem |
|--------|---------|
| Confluent Cloud | Best production option, but paid plans start at $400+/month |
| Apache Kafka self-hosted | Needs Docker or JVM; user preference was managed cloud |
| Redpanda Cloud | Free tier, no credit card, Kafka-compatible wire protocol |

**Fix:**
Switched to Redpanda Cloud. Same `confluent-kafka` Python library, same API,
same Kafka concepts (topics, consumer groups, offsets, ACLs). Only the
bootstrap server URL changed.

**Lesson:**
Free tier services can change without warning. Always check if the free tier
still exists before building on it. When evaluating alternatives, prefer
solutions that are wire-compatible with the original — the code change was
one line (the bootstrap server URL).

---

## Problem 7 — Evidently 0.7.x completely changed its API

**When:** Writing and running `src/monitoring/drift.py`.

**First error:**
```
ModuleNotFoundError: No module named 'evidently.metric_preset'
```

We wrote:
```python
from evidently.metric_preset import DataDriftPreset
from evidently.report import Report
from evidently import ColumnMapping
```

**Why it happened:**
Evidently released a major API overhaul in 0.7.x. The entire module structure
changed. These were the correct imports in 0.4.x-0.6.x.

**How we found the fix:**
Explored the installed package structure:
```python
import pkgutil, evidently
[m.name for m in pkgutil.iter_modules(evidently.__path__)]
# ['presets', 'metrics', 'core', 'legacy', ...]
```

Tested imports interactively until finding the correct paths:
```python
from evidently import Report          # was: from evidently.report import Report
from evidently.presets import DataDriftPreset  # was: from evidently.metric_preset import DataDriftPreset
# ColumnMapping not needed in 0.7.x
```

**Second change — `run()` now returns a Snapshot:**
In 0.6.x and earlier:
```python
report = Report(metrics=[DataDriftPreset()])
report.run(reference_data=ref, current_data=cur)
report.save_html("report.html")  # called on the report
result = report.as_dict()
```

In 0.7.x:
```python
snapshot = Report([DataDriftPreset()]).run(reference_data=ref, current_data=cur)
# run() returns a Snapshot object, not None
snapshot.save_html("report.html")  # called on the snapshot
result = snapshot.dict()
```

**Third error:**
```
ValueError: Column (created_at) is partially present in data
```
The current dataframe had a `created_at` column that the reference didn't have.
Evidently tried to drift-check every column it found across both datasets.

**Fix:**
```python
snapshot = Report([DataDriftPreset()]).run(
    reference_data=reference_df[["label", "confidence"]],
    current_data=current_df[["label", "confidence"]],
)
```
Only pass the columns you actually want to monitor.

**Lesson:**
When a library releases a major version, don't assume the import paths or
method names are stable. Pin your versions in requirements files, and when
you upgrade, treat it as a rewrite of the integration code — read the
changelog and test from scratch.

---

## Problem 8 — Parsing bug: terminal said wrong column drifted

**When:** Reading the terminal output from `drift.py`.

**What happened:**
Terminal output: `drifted columns: ['label']`
HTML report showed: confidence drifted, label did not.

**Why it happened:**
My `extract_drift_result` function assumed all drift scores in the Evidently
dict are **p-values** — where a low value (< 0.05) means drift detected.

But Evidently automatically chooses the statistical test based on the data type:
- Numerical columns → Wasserstein distance (normed). Range 0–1. **Higher = more drift.**
- Categorical columns → Jensen-Shannon distance. Range 0–1. **Higher = more drift.**

Both are *distance* scores, not p-values. My code checked `value < 0.05`
which accidentally flagged low Jensen-Shannon distance (label, no drift)
and missed high Wasserstein distance (confidence, actual drift).

**Correct results from the HTML report:**
- `confidence`: Wasserstein = 0.910 → **Drift detected** (very high distance)
- `label`: Jensen-Shannon = 0.023 → **No drift** (very low distance, distributions nearly identical)

**Fix:**
The HTML report is the source of truth. For programmatic drift detection,
rely on the `DriftedColumnsCount` metric in the snapshot dict (Evidently
computes this correctly internally). Don't reparse individual column scores
unless you account for the stat test method used per column.

**Lesson:**
Always cross-check terminal/log output against the actual artifact (HTML report,
database row, API response). Parsing complex nested results is error-prone —
prefer letting the library compute the final verdict and only extracting that,
rather than trying to reproduce its logic.

---

## Problem 9 — Confidence drift detected from a synthetic reference

**When:** Interpreting the drift report.

**What happened:**
Evidently flagged confidence as drifted with Wasserstein distance = 0.910
(very high). Visually, the confidence scores looked fine — mean 0.970,
matching the reference.

**Why it happened:**
We built the reference confidence distribution synthetically:
```python
confidences = np.clip(normal(mean=0.97, std=0.05, n=1900), 0.5, 1.0)
```

This creates a **smooth bell curve** centered at 0.97.

But real DistilBERT confidence scores are **bimodal** — not a bell curve:
- Most articles: 0.98–1.00 (model is very certain)
- Some ambiguous articles: 0.65–0.80 (model is uncertain)
- Very few in the middle range

The shape mismatch between a synthetic normal curve and a bimodal real
distribution is what Wasserstein distance picks up. Even though the means
match, the distributions look completely different.

**The right approach:**
The reference should be built from *actual model outputs* on the test set,
not synthesized. Run the full AG News test set through the FastAPI server,
collect real confidence scores, and use those as the reference. That captures
the true shape of the distribution.

**Why we didn't do this:**
Running 7,600 articles through the API would take ~2 hours (at 1 msg/sec).
Storing those results as a reference file is a separate engineering task.
For a portfolio project, the synthetic reference is enough to demonstrate the
concept. The first real improvement in a production setting would be replacing
it with an actual baseline run.

**Trade-off accepted:**
Synthetic reference → fast to build, demonstrates the concept, but creates
a false positive for confidence drift. Real reference → accurate, but requires
a dedicated baseline run before monitoring can start.

---

## Summary — what's working end-to-end

```
AG News dataset (120,000 articles)
        ↓
Fine-tuned DistilBERT (distilbert-base-uncased)
        ↓
94.64% accuracy, 94.65% F1 on 4-class news classification
        ↓ (pushed to HuggingFace Hub)
FastAPI server (loads model once at startup, serves in 6–25ms)
        ↓ (Prometheus /metrics endpoint for Grafana)
POST /classify → {label, confidence, latency_ms}
        ↑
Consumer (reads from Kafka, checks Redis cache first)
        ↑
Redpanda Cloud topic: content-stream
        ↑
Producer (streams AG News articles at 1 msg/sec)

All classifications → SQLite (classifications.db)
SQLite → Evidently drift report → MLflow on DagsHub
```

**Numbers from our test run:**
- Pipeline lag: ~230ms end-to-end (Kafka → classify → commit)
- Cache hit latency: <1ms
- Model latency: 13–37ms on CPU
- Drift check: runs in seconds, produces HTML report + logs to MLflow

**What's not built yet:**
- Grafana Cloud dashboard (Prometheus metrics → visual panels)
- GitHub Actions (automated drift check, CI on push)
- Dockerfile + HF Spaces deployment
- Project README with architecture diagram
