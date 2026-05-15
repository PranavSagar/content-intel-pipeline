# Content Intelligence Pipeline

A production-grade MLOps pipeline that classifies news articles in real time.
Fine-tuned DistilBERT served via FastAPI, streamed through Kafka, monitored with
Evidently and Grafana — every layer built with the tooling used at production scale.

**Live demo**: `POST https://pranavsagar10-content-intel-classifier.hf.space/classify`

---

## What it does

Classifies any news text into one of four categories with **94.64% accuracy**:

| Label | Example |
|-------|---------|
| World | "UN Security Council meets over Ukraine ceasefire talks" |
| Sports | "Ronaldo scores hat trick in Champions League final" |
| Business | "Apple quarterly earnings beat analyst expectations" |
| Sci/Tech | "NASA Mars rover discovers signs of ancient microbial life" |

```bash
curl -X POST https://pranavsagar10-content-intel-classifier.hf.space/classify \
  -H "Content-Type: application/json" \
  -d '{"text": "Federal Reserve raises interest rates by 25 basis points"}'

# {"label": "Business", "confidence": 0.99, "latency_ms": 52.3}
```

---

## Architecture

```
AG News dataset (120,000 articles)
        │
        ▼
┌─────────────────┐
│  Fine-tuning    │  DistilBERT → 94.64% accuracy
│  (train.py)     │  Tracked in MLflow on DagsHub
└────────┬────────┘
         │ push model
         ▼
┌─────────────────────────────────────────────────────────┐
│  HuggingFace Hub                                        │
│  pranavsagar10/content-classifier-distilbert            │
└────────┬────────────────────────────────────────────────┘
         │ load at startup
         ▼
┌─────────────────┐     ┌──────────────┐
│  FastAPI server │────▶│  Prometheus  │──▶ Grafana Cloud
│  POST /classify │     │  /metrics    │    dashboard
│  GET /health    │     └──────────────┘
└────────▲────────┘
         │ HTTP classify
         │
┌────────┴────────┐     ┌──────────────┐
│  Consumer       │────▶│  Redis cache │  (Upstash)
│  (consumer.py)  │     │  TTL 1h      │
└────────▲────────┘     └──────────────┘
         │                      │
         │                      ▼
┌────────┴────────┐     ┌──────────────┐
│  Redpanda Cloud │     │  SQLite DB   │──▶ Evidently
│  content-stream │     │  (results)   │    drift report
└────────▲────────┘     └──────────────┘       │
         │                                      ▼
┌────────┴────────┐                      MLflow on DagsHub
│  Producer       │
│  (producer.py)  │
└─────────────────┘
```

---

## Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Model | DistilBERT (HuggingFace) | 97% of BERT accuracy at 40% the size |
| Training | PyTorch + HuggingFace Trainer | Industry standard, MPS acceleration on Mac |
| Experiment tracking | MLflow → DagsHub | Free managed MLflow, no infra to run |
| Serving | FastAPI + uvicorn | Async, typed, auto-generates OpenAPI docs |
| Deployment | HuggingFace Spaces (Docker) | Free public hosting, git-based deploy |
| Broker | Redpanda Cloud | Kafka-compatible, free tier, no JVM |
| Cache | Redis (Upstash) | Avoids re-classifying identical articles |
| Storage | SQLite | Zero-setup local persistence |
| Metrics | Prometheus client | Standard format, scraped by Grafana Alloy |
| Dashboards | Grafana Cloud | Live latency/throughput/error panels |
| Drift detection | Evidently | Statistical tests on label + confidence distribution |
| CI | GitHub Actions | Lint, import checks, smoke test on every push |
| CD | GitHub Actions → HF Spaces | Auto-deploys on serving code changes |

---

## Results

| Metric | Value |
|--------|-------|
| Test accuracy | **94.64%** |
| Test F1 (macro) | **94.65%** |
| Training time | ~82 min (Apple M-series MPS) |
| Serving latency p50 | ~19ms (CPU) |
| Serving latency p95 | ~23ms (CPU) |
| Pipeline lag (Kafka → classify) | ~230ms |
| Cache hit latency | <1ms |

---

## Live links

| Resource | URL |
|----------|-----|
| Live demo UI | https://pranavsagar.github.io/classify/ |
| API (HF Spaces) | https://pranavsagar10-content-intel-classifier.hf.space |
| API docs (Swagger) | https://pranavsagar10-content-intel-classifier.hf.space/docs |
| Model (HF Hub) | https://huggingface.co/pranavsagar10/content-classifier-distilbert |
| MLflow experiments | https://dagshub.com/PranavSagar/content-intel-pipeline.mlflow |
| Grafana dashboard | https://pranavsagar.grafana.net/d/content-intel-pipeline |

---

## Project structure

```
content-intel-pipeline/
│
├── src/
│   ├── training/
│   │   ├── dataset.py          # AG News loader + tokenizer
│   │   └── train.py            # Fine-tuning loop with MLflow tracking
│   ├── serving/
│   │   └── app.py              # FastAPI app with Prometheus metrics
│   ├── pipeline/
│   │   ├── producer.py         # Kafka producer — streams articles at 1 msg/sec
│   │   └── consumer.py         # Kafka consumer — classify → Redis → SQLite
│   └── monitoring/
│       └── drift.py            # Evidently drift report → MLflow
│
├── configs/
│   └── training_config.yaml    # All hyperparameters in one place
│
├── grafana/
│   └── dashboard.json          # Importable Grafana dashboard (6 panels)
│
├── docs/
│   ├── architecture.md         # Technical HLD + LLD with Mermaid diagrams
│   ├── architecture-explained.md # Same architecture in plain English
│   ├── concepts/               # Plain English + technical explanations
│   │   ├── fine-tuning.md
│   │   ├── transformer-end-to-end.md
│   │   ├── serving-layer.md
│   │   ├── kafka-pipeline.md
│   │   ├── drift-monitoring.md
│   │   └── grafana-monitoring.md
│   └── build-log.md            # 15 real problems hit + how each was solved
│
├── .github/workflows/
│   ├── ci.yml                  # Lint + import checks on every push
│   ├── drift_check.yml         # Weekly Evidently drift check → MLflow
│   └── deploy_spaces.yml       # Auto-deploy to HF Spaces on serving changes
│
├── Dockerfile                  # CPU-only, port 7860, HF Spaces compatible
├── requirements-training.txt
├── requirements-serving.txt
├── requirements-pipeline.txt
├── requirements-monitoring.txt
└── requirements-dev.txt        # ruff linter
```

---

## Running locally

### Prerequisites
```bash
git clone https://github.com/PranavSagar/content-intel-pipeline
cd content-intel-pipeline
python -m venv .venv && source .venv/bin/activate
```

Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

### 1 — Train the model (optional — pre-trained model is on HF Hub)
```bash
pip install -r requirements-training.txt
caffeinate -i python -m src.training.train   # caffeinate prevents Mac sleep
```

### 2 — Run the FastAPI server
```bash
pip install -r requirements-serving.txt
python -m uvicorn src.serving.app:app --reload
# → http://localhost:8000/docs
```

### 3 — Run the Kafka pipeline
Requires Redpanda/Kafka credentials in `.env`.
```bash
pip install -r requirements-pipeline.txt

# Terminal 1 — consumer (start before producer)
python -m src.pipeline.consumer

# Terminal 2 — producer
python -m src.pipeline.producer
```

### 4 — Run drift monitoring
```bash
pip install -r requirements-monitoring.txt
python -m src.monitoring.drift --hours 24
# → reports/drift_YYYYMMDD_HHMM.html
```

### 5 — Run with Docker
```bash
docker build -t content-intel .
docker run -p 8000:7860 -e HF_MODEL_ID=pranavsagar10/content-classifier-distilbert content-intel
```

---

## Key design decisions

**Why DistilBERT over BERT?**
97% of BERT's accuracy at 60% the parameters and 2× the inference speed. The
4% accuracy gap is not worth the compute cost for a 4-class classification task.

**Why Kafka (Redpanda) instead of direct API calls?**
Decoupling. The producer doesn't care how fast the consumer is. If the classifier
is slow or restarting, messages queue in the topic — nothing is lost.
Also enables replay: rewind the topic and re-classify with a new model version.

**Why Redis cache?**
News wire services distribute the same story to hundreds of outlets. Identical
articles appear repeatedly in the stream. Cache hit returns in <1ms vs 20ms
model inference.

**Why manual Kafka commit (not auto-commit)?**
Auto-commit marks messages as done before processing completes. A crash between
auto-commit and DB write = silent data loss. Manual commit = at-least-once
delivery. Duplicate processing (on crash/replay) is harmless; missing data is not.

**Why Evidently for drift monitoring?**
Zero infrastructure — runs as a Python script. Generates HTML reports and logs
to MLflow. Statistical tests (Jensen-Shannon for labels, Wasserstein for
confidence scores) are more reliable than eyeballing charts.

---

## Documentation

| Document | What's in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Technical HLD + LLD. System context, component view, sequence diagrams for every flow (inference, Kafka, drift, observability, CI/CD), data model, failure modes, deployment topology. All diagrams in Mermaid so they're versioned and editable in PRs. |
| [`docs/architecture-explained.md`](docs/architecture-explained.md) | The same architecture in plain English. Uses a newsroom analogy and walks one headline through the system end-to-end. |
| [`docs/concepts/`](docs/concepts/) | Per-component deep dives with both plain-English analogies and technical explanations — written while building, not reconstructed after. |
| [`docs/build-log.md`](docs/build-log.md) | 15 real problems hit during development with root cause, fix, and lesson for each. |

---

## CI status

![CI](https://github.com/PranavSagar/content-intel-pipeline/actions/workflows/ci.yml/badge.svg)
