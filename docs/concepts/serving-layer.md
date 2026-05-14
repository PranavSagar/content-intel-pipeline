# The Serving Layer — FastAPI + Prometheus

## What we built

A web server that takes a piece of text, runs it through the trained model,
and returns a prediction. Anyone (or any service) can call it over HTTP.

```
POST /classify
{"text": "NASA launches new telescope"}
        ↓
{ "label": "Sci/Tech", "confidence": 0.97, "latency_ms": 24.88 }
```

---

## Why FastAPI and not Flask?

You already use Flask in your resume (release automation platform). So why FastAPI here?

| | Flask | FastAPI |
|---|---|---|
| Speed | Slower | ~2-3x faster (async-native) |
| Request validation | Manual | Automatic via Pydantic |
| API docs | Manual | Auto-generated at /docs |
| Type hints | Optional | Required (forces cleaner code) |
| Age | 2010 | 2018 |

**Plain English**: Flask is a kitchen knife — universal, gets the job done.
FastAPI is a chef's knife — same job, better tool for professional use.

The auto-generated docs at `/docs` matter for portfolio — anyone visiting your
Hugging Face Space can try the API in a browser without writing any code.

---

## Decision 1 — Loading the model once at startup

### Plain English
Imagine you run a restaurant. Every time a customer orders food, you don't:
- Drive to the farm
- Buy the ingredients
- Drive back
- Then cook

You buy ingredients once, keep them in the kitchen, and cook from stock.

Loading the model is the "drive to the farm" — it downloads 268MB and loads
66 million weights into RAM. At 6-25ms per request, if you did this per-request
it would take 30+ seconds per call instead.

**Load once, serve forever.**

### Technical detail
FastAPI uses a `lifespan` context manager — code before `yield` runs at startup,
code after `yield` runs at shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    model_store["classifier"] = pipeline(...)   # runs once at startup
    yield                                        # app handles requests here
    model_store.clear()                          # runs at shutdown
```

We store the loaded model in a module-level dict (`model_store`). Every request
handler reads from this dict — zero model loading cost per request.

**Your backend analogy**: this is exactly a connection pool.
Open connections once at startup (`HikariCP` in Spring Boot),
reuse them per-request, close them at shutdown.

---

## Decision 2 — CPU for serving, not MPS (GPU)

### Plain English
GPUs are like buses — they carry many passengers (data) very efficiently,
but they have a fixed route and a boarding process. For a bus carrying
1000 people it's great. For 1 person? It's slower than a taxi.

Training processes 32 articles at once (a batch). GPU excels at this.
Serving processes 1 article at a time per request. CPU is actually faster
for single-item inference because there's no GPU-boarding overhead.

Also: MPS is Apple-specific. When we deploy to Hugging Face Spaces (Linux servers),
there's no MPS. CPU works everywhere.

### Technical detail
```python
model_store["classifier"] = pipeline(
    "text-classification",
    model=model_id,
    device=-1,    # -1 = CPU. 0 = first GPU. "mps" = Apple Silicon.
)
```

For DistilBERT on a single inference: CPU gives 6-25ms.
Adding MPS overhead for a single item would actually be slower.

GPU/MPS becomes worth it when you batch incoming requests together
(called "dynamic batching") — an advanced serving pattern we're not
implementing here.

---

## Decision 3 — Prometheus metrics

### Plain English
Prometheus is a monitoring system. It works by **pulling** data from your app
on a schedule (every 15 seconds by default). Your app exposes a `/metrics`
endpoint that Prometheus reads.

Think of it like this: Prometheus knocks on your door every 15 seconds
and asks "what are your current counts?" Your app answers with a text report.

Grafana then reads from Prometheus and draws charts.

```
Your app (/metrics) ← Prometheus (scrapes every 15s) ← Grafana (reads & draws charts)
```

We track three things:

**Counter — requests per label**
A counter only goes up. Tracks how many times each label was predicted.
Useful to spot if the model is heavily biased toward one category in production.

```
classify_requests_total{label="Sci/Tech"} 1.0
classify_requests_total{label="Sports"} 1.0
classify_requests_total{label="Business"} 1.0
```

**Histogram — latency**
Tracks the distribution of response times. Not just the average — the full
picture. This is how you get p95/p99 latency (the number your Glance SLAs
probably track).

```python
buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]  # seconds
```
"What % of requests completed in under 50ms?" — a histogram answers this.
An average cannot.

**Counter — errors**
Total errors. If this starts climbing, something is wrong. Alert on it.

### Technical detail
```python
from prometheus_client import Counter, Histogram, generate_latest

REQUESTS = Counter("classify_requests_total", "...", ["label"])
LATENCY  = Histogram("classify_latency_seconds", "...", buckets=[...])

# In the handler:
REQUESTS.labels(label=result["label"]).inc()    # increment for this label
LATENCY.observe(latency_seconds)                # record this request's time

# The /metrics endpoint:
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

`generate_latest()` serializes all metrics into Prometheus text format.

---

## Decision 4 — /health returns 503 until model is ready

### Plain English
Imagine a restaurant opens at 9am but the chef arrives at 9:10am.
If customers walk in at 9:05, the kitchen can't serve them — even though
the door is open.

The `/health` endpoint is how the outside world knows if you're actually
ready to serve requests, not just running.

We return `200 OK` only when the model is loaded. During startup (before
the model finishes loading), we return `503 Service Unavailable`.

Load balancers check `/health` before routing traffic. Without this,
requests would arrive before the model is ready and fail.

### Technical detail
```python
@app.get("/health")
def health():
    if "classifier" not in model_store:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ok"}
```

---

## The full request lifecycle

```
Client sends: POST /classify {"text": "NASA launches telescope"}
        ↓
Pydantic validates: is `text` present? is it a string?
        ↓
Handler starts timer (time.perf_counter)
        ↓
model_store["classifier"](text, truncation=True, max_length=128)
  ↓
  tokenizer converts text → token IDs
  ↓
  DistilBERT forward pass (6 attention layers)
  ↓
  classification head → logits → softmax → label + score
        ↓
Handler stops timer → records latency
        ↓
Prometheus counters incremented
        ↓
Client receives: {"label": "Sci/Tech", "confidence": 0.97, "latency_ms": 24.88}
```

---

## What we tested

Three articles, three categories — all correct:

| Text | Expected | Got | Confidence | Latency |
|---|---|---|---|---|
| NASA launches telescope | Sci/Tech | Sci/Tech ✓ | 97.45% | 24.88ms |
| Manchester United wins | Sports | Sports ✓ | 75.99% | 6.56ms |
| Fed raises interest rates | Business | Business ✓ | 99.15% | 7.52ms |

The first request is slower (24ms) because PyTorch does some one-time JIT
compilation on the first forward pass. Subsequent requests stabilize at 6-8ms.

---

## Trade-offs we accepted

**No authentication on /classify**
For a portfolio project this is fine. In production you'd add an API key
header or OAuth. FastAPI has built-in dependency injection for this.

**Single worker**
We run one uvicorn worker. Multiple concurrent requests would queue.
In production: `uvicorn app:app --workers 4` or use Gunicorn as a process manager.

**No request batching**
We process one article per request. Dynamic batching (grouping concurrent requests
into one model forward pass) would improve throughput significantly at high load.
That's an advanced pattern — not needed for this project.

**CPU only**
Covered above. Right choice for this scale. Wrong choice at 100K+ RPS.
