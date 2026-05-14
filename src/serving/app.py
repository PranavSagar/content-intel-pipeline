import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from transformers import pipeline

load_dotenv(dotenv_path=".env")

# ── Prometheus metrics ─────────────────────────────────────────────────────────
# Counter: monotonically increasing. Good for "how many times did X happen?"
# Histogram: tracks distribution of values. Good for latency (gives you p50/p95/p99).

REQUESTS = Counter(
    "classify_requests_total",
    "Total classification requests",
    ["label"],          # one counter per predicted label — lets you see label distribution
)
LATENCY = Histogram(
    "classify_latency_seconds",
    "End-to-end classification latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],   # bucket boundaries in seconds
)
ERRORS = Counter(
    "classify_errors_total",
    "Total classification errors",
)

# ── Model store ────────────────────────────────────────────────────────────────
# A plain dict used as a module-level container for the loaded model.
# This is the "kept in memory" part — model is loaded once and lives here
# for the entire lifetime of the process.
model_store: dict = {}


# ── Lifespan ───────────────────────────────────────────────────────────────────
# FastAPI's lifespan runs the code before `yield` at startup and after `yield`
# at shutdown. Equivalent to @PostConstruct / @PreDestroy in Spring Boot.
@asynccontextmanager
async def lifespan(app: FastAPI):
    model_id = os.environ.get("HF_MODEL_ID", "pranavsagar10/content-classifier-distilbert")
    print(f"Loading model: {model_id}")

    model_store["classifier"] = pipeline(
        "text-classification",
        model=model_id,
        device=-1,      # -1 = CPU. For serving we use CPU — MPS/CUDA is for training.
    )
    print("Model loaded and ready.")
    yield                               # app runs here, handling requests
    model_store.clear()                 # cleanup on shutdown
    print("Model unloaded.")


# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Content Intelligence API",
    description="Classifies news text into World / Sports / Business / Sci/Tech",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Request / Response schemas ─────────────────────────────────────────────────
# Pydantic models validate the request body automatically.
# If `text` is missing or not a string, FastAPI returns a 422 before your code runs.
class ClassifyRequest(BaseModel):
    text: str

class ClassifyResponse(BaseModel):
    label: str
    confidence: float
    latency_ms: float


# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest):
    if not req.text.strip():
        raise HTTPException(status_code=422, detail="text cannot be empty")

    start = time.perf_counter()

    try:
        result = model_store["classifier"](req.text, truncation=True, max_length=128)[0]
    except Exception as e:
        ERRORS.inc()
        raise HTTPException(status_code=500, detail=str(e))

    latency_s = time.perf_counter() - start

    REQUESTS.labels(label=result["label"]).inc()
    LATENCY.observe(latency_s)

    return ClassifyResponse(
        label=result["label"],
        confidence=round(result["score"], 4),
        latency_ms=round(latency_s * 1000, 2),
    )


@app.get("/health")
def health():
    # Load balancers call this to decide whether to send traffic here.
    # Returns 200 only when the model is actually loaded — not just when the
    # process is alive. That distinction matters during startup.
    model_ready = "classifier" in model_store
    if not model_ready:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ok", "model": os.environ.get("HF_MODEL_ID", "unknown")}


@app.get("/metrics")
def metrics():
    # Prometheus scrapes this endpoint on a schedule (e.g. every 15s).
    # Grafana reads from Prometheus. This is the starting point of that chain.
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
