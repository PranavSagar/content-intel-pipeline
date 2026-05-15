---
title: Content Intel Classifier
emoji: 🔍
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
app_port: 7860
---

# Content Intel Pipeline — News Classifier

A real-time news article classifier built with fine-tuned DistilBERT, served via FastAPI.

## What it does

Classifies any news text into one of four categories:
- **World** — international news, politics, conflict
- **Sports** — matches, athletes, leagues
- **Business** — markets, earnings, economy
- **Sci/Tech** — technology, science, research

Trained on [AG News](https://huggingface.co/datasets/ag_news) — **94.64% accuracy**.

## API

### POST /classify
```json
{"text": "Apple announces record quarterly earnings beating analyst expectations"}
```
```json
{"label": "Sci/Tech", "confidence": 0.99, "latency_ms": 18.4}
```

### GET /health
Returns `{"status": "ok"}` when the model is loaded and ready.

### GET /metrics
Prometheus metrics endpoint — classification counts, latency histograms, error counts.

## Stack
- **Model**: DistilBERT fine-tuned on AG News → [pranavsagar10/content-classifier-distilbert](https://huggingface.co/pranavsagar10/content-classifier-distilbert)
- **Serving**: FastAPI + uvicorn
- **Observability**: Prometheus metrics → Grafana Cloud
- **Pipeline**: Redpanda (Kafka) → Redis cache → SQLite
- **Monitoring**: Evidently drift detection → MLflow on DagsHub
