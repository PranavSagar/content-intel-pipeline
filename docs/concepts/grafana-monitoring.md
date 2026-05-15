# Grafana Monitoring — Metrics Dashboard

## What problem are we solving?

Your model is deployed. Requests are coming in. How do you know it's working well?

Option 1: Read the logs.
```
[consumer] classified → Sci/Tech (0.99) 18ms
[consumer] classified → Business (0.97) 22ms
[consumer] classified → World (1.00) 15ms
```
This works for 10 requests. It doesn't work for 10,000 requests per minute.
Nobody reads individual log lines at production volume.

Option 2: A live dashboard.
- p95 latency has been climbing from 20ms to 45ms over the last 2 hours
- Sci/Tech labels went from 25% to 60% of traffic in the last hour
- 0 errors in the last 24 hours

One tells you what happened. The other tells you what's *trending* — before
it becomes a problem.

---

## The full picture — how it all connects

```
FastAPI server
(localhost:8000/metrics)
        │
        │  Every 15 seconds, Alloy polls this URL
        │  and collects all the numbers
        ▼
Grafana Alloy
(running on your laptop, installed via brew)
        │
        │  Alloy pushes collected metrics over HTTPS
        │  to Grafana Cloud (called "remote_write")
        ▼
Grafana Cloud Prometheus
(managed database that stores time-series metrics)
        │
        │  Grafana dashboard queries this database
        │  using PromQL (Prometheus Query Language)
        ▼
Grafana Dashboard
(live in your browser at pranavsagar.grafana.net)
```

Three separate systems working together:
1. **FastAPI** — produces the metrics
2. **Grafana Alloy** — collects and ships them
3. **Grafana Cloud** — stores and visualises them

---

## What are "metrics"? What is Prometheus?

### Plain English
Every time someone calls `/classify`, FastAPI increments counters and
records timings. These are called metrics — numbers that describe your
system's behaviour, tracked over time.

Think of it like a car dashboard. The speedometer doesn't record every
individual wheel rotation — it shows you the current speed derived from
those rotations. The fuel gauge doesn't log every litre burned — it shows
remaining fuel as a number.

Metrics are the same idea: simple numbers that summarise what's happening.

### What is Prometheus?

Prometheus is two things:
1. **A format** — a standard text format for exposing metrics over HTTP
2. **A protocol** — a pull-based system where a collector periodically
   scrapes (fetches) metrics from your service

When you visit `http://localhost:8000/metrics`, you see raw Prometheus format:
```
# HELP classify_requests_total Total classification requests
# TYPE classify_requests_total counter
classify_requests_total{label="World"} 47.0
classify_requests_total{label="Sports"} 39.0
classify_requests_total{label="Business"} 52.0
classify_requests_total{label="Sci/Tech"} 61.0

# HELP classify_latency_seconds Latency of classify endpoint in seconds
# TYPE classify_latency_seconds histogram
classify_latency_seconds_bucket{le="0.005"} 0.0
classify_latency_seconds_bucket{le="0.01"} 12.0
classify_latency_seconds_bucket{le="0.025"} 189.0
classify_latency_seconds_bucket{le="0.05"} 199.0
classify_latency_seconds_bucket{le="+Inf"} 199.0
classify_latency_seconds_count 199.0
classify_latency_seconds_sum 3.847
```

Every number has a name, a type (`counter`, `histogram`, `gauge`), and
optional labels (key-value pairs like `label="World"`).

### The three metric types we use

**Counter** (`classify_requests_total`, `classify_errors_total`)
Only goes up. Never decreases (except on restart). You derive rate from it.
"How many requests in the last 5 minutes?" = today's count minus 5-minutes-ago count.

**Histogram** (`classify_latency_seconds`)
Records observations in pre-defined buckets. Bucket `le="0.025"` counts
how many requests finished in ≤ 25ms. From buckets you can estimate
percentiles (p50, p95, p99) — Prometheus can't store every individual timing,
so it approximates them from bucket counts.

**Gauge** (not used here, but worth knowing)
Can go up and down. Used for things like "current memory usage", "active connections".

---

## The three components in detail

### Component 1 — FastAPI `/metrics` endpoint

In `src/serving/app.py` we create three Prometheus metric objects:
```python
from prometheus_client import Counter, Histogram, make_asgi_app

REQUESTS = Counter("classify_requests", "Total requests", ["label"])
LATENCY  = Histogram("classify_latency_seconds", "Request latency")
ERRORS   = Counter("classify_errors", "Total errors")
```

`prometheus_client` is a Python library that:
- Manages the internal counters/buckets in memory
- Exposes them at `/metrics` in Prometheus text format automatically
- Thread-safe (multiple requests updating the same counter simultaneously is fine)

When we call:
```python
REQUESTS.labels(label=result["label"]).inc()    # +1 to the right label bucket
LATENCY.observe(elapsed)                         # record this timing in the histogram
```

...the library updates its in-memory state. The next time `/metrics` is polled,
it reads that state and formats it as text.

---

### Component 2 — Grafana Alloy (the bridge)

#### Plain English
Grafana Cloud lives on the internet. Your FastAPI runs on localhost.
The internet cannot reach localhost. You need something that runs locally,
reads from localhost, and pushes to the internet.

That's Alloy. Think of it as a courier who sits in your building, picks up
packages from your desk every 15 minutes, and delivers them to a central
warehouse (Grafana Cloud).

#### Technical detail
Alloy is an open-source telemetry collector written in Go. It replaced the
older "Grafana Agent" product. Installed via:
```bash
brew install grafana/grafana/alloy
brew services start grafana/grafana/alloy  # runs in background, survives restarts
```

It's configured with a `.alloy` file at `/opt/homebrew/etc/alloy/config.alloy`.

Our config has two blocks:

**Block 1 — `prometheus.scrape`** (the collector)
```hcl
prometheus.scrape "fastapi" {
  targets         = [{"__address__" = "localhost:8000"}]
  scrape_interval = "15s"
  metrics_path    = "/metrics"
  forward_to      = [prometheus.remote_write.metrics_hosted_prometheus.receiver]
}
```
Every 15 seconds: GET `http://localhost:8000/metrics` → parse the text format
→ pass the data to the remote_write block.

**Block 2 — `prometheus.remote_write`** (the shipper)
```hcl
prometheus.remote_write "metrics_hosted_prometheus" {
  endpoint {
    url = "https://prometheus-prod-43-prod-ap-south-1.grafana.net/api/prom/push"
    basic_auth {
      username = "3212468"         # your numeric Grafana Cloud user ID
      password = "<api-token>"     # API token generated from Grafana Cloud UI
    }
  }
}
```
Takes whatever scrape gave it → sends a compressed HTTP POST to Grafana Cloud's
Prometheus ingest endpoint → Grafana Cloud stores the data with a timestamp.

The two blocks are connected: `forward_to = [...receiver]` wires scrape output
into remote_write input. Alloy's config language makes this explicit rather than
implicit.

#### Why Alloy and not just Prometheus?
A standard Prometheus server is a **pull** system — it scrapes and stores locally.
To get data into Grafana Cloud you'd need to run Prometheus locally AND configure
it to remote_write. Alloy skips the local storage entirely — it scrapes and
immediately forwards. Fewer moving parts.

---

### Component 3 — Grafana Cloud

#### Plain English
Grafana Cloud is two products in one:
- **Managed Prometheus** — stores your metrics with timestamps, queryable with PromQL
- **Grafana dashboards** — browser UI that runs PromQL queries and draws charts

You don't install or manage anything. Grafana Labs runs the infrastructure.
You just send data and query it.

#### The data flow inside Grafana Cloud
```
Alloy → remote_write → Grafana Cloud Prometheus (storage)
                                    ↓
                        Grafana dashboard queries run PromQL
                                    ↓
                        Results drawn as time-series charts
```

---

## How we connected everything — step by step

This is the actual sequence we followed:

**Step 1** — Signed up for Grafana Cloud at grafana.com. Free tier, no credit card.
Our instance: `pranavsagar.grafana.net`.

**Step 2** — In Grafana Cloud UI: Connections → Add new connection → Hosted Prometheus Metrics → Via Grafana Alloy.

**Step 3** — Generated an API token on that page:
- Token name: `content-intel-alloy`
- Scope: `set:alloy-data-write` (write-only — least privilege)
- This token is the password in the `basic_auth` block

**Step 4** — Copied the remote_write URL from the UI. It's region-specific:
`https://prometheus-prod-43-prod-ap-south-1.grafana.net/api/prom/push`
The `-ap-south-1` indicates Grafana placed our stack in AWS ap-south-1 (Mumbai),
closest to our location.

**Step 5** — Installed Alloy via brew. Config file placed at:
`/opt/homebrew/etc/alloy/config.alloy`
We wrote our two-block config (scrape + remote_write) into this file.

**Step 6** — Started Alloy as a background service:
```bash
brew services start grafana/grafana/alloy
```
Alloy started scraping `/metrics` every 15 seconds.

**Step 7** — Waited 30 seconds (two scrape cycles) → went to Grafana Explore
→ queried `classify_requests_total` → data appeared.

**Step 8** — Built and imported `grafana/dashboard.json` (6 panels).

---

## The 6 panels and the PromQL behind them

### Panel 1 — Classification Rate (per minute, by label)
```promql
sum by (label) (rate(classify_requests_total[5m])) * 60
```

**Plain English:** "How many classifications per minute are we doing, broken down by label?"

- `classify_requests_total` — a counter (ever-increasing)
- `rate(...[5m])` — computes per-second rate using the last 5 minutes of data
- `* 60` — converts per-second to per-minute (easier to read)
- `sum by (label)` — splits into one line per label (World, Sports, Business, Sci/Tech)

**What to watch:** A sudden drop to 0 on all labels means the producer stopped
or the consumer crashed. A sustained spike in one label means input distribution
shifted — which is what Evidently also detects, but here you see it in real time.

---

### Panel 2 — Latency Percentiles (p50, p95, p99)
```promql
histogram_quantile(0.50, rate(classify_latency_seconds_bucket[5m])) * 1000
histogram_quantile(0.95, rate(classify_latency_seconds_bucket[5m])) * 1000
histogram_quantile(0.99, rate(classify_latency_seconds_bucket[5m])) * 1000
```

**Plain English:** "Of all requests in the last 5 minutes:
- p50 = the middle request took how long?
- p95 = the slowest 5% of requests took how long?
- p99 = the slowest 1% of requests took how long?"

- `classify_latency_seconds_bucket` — the histogram bucket counts
- `rate(...[5m])` — rate of observations per bucket per second
- `histogram_quantile(0.95, ...)` — estimates the 95th percentile from bucket counts
- `* 1000` — seconds to milliseconds

**Why percentiles matter over averages:** An average can hide bad behaviour.
If 95% of requests take 10ms and 5% take 2000ms, average = ~110ms. The average
looks fine. The p99 would show 2000ms — a real problem.

**From our test:** p50 ~19ms, p95 ~23ms. Healthy for CPU inference.

---

### Panel 3 — Label Distribution (pie chart)
```promql
sum by (label) (increase(classify_requests_total[1h]))
```

**Plain English:** "Of all articles classified in the last hour, what % were each label?"

- `increase(...[1h])` — total count added in the last hour (not a rate)
- `sum by (label)` — one slice per label

This is the visual version of what Evidently checks statistically. If Sci/Tech
goes from 25% to 80%, something changed — either the input stream changed, or
the model started misclassifying.

---

### Panel 4 — Total Requests (stat)
```promql
sum(classify_requests_total)
```
Cumulative count since the server started. Quick sanity check.

---

### Panel 5 — p95 Latency stat with colour thresholds
```promql
histogram_quantile(0.95, rate(classify_latency_seconds_bucket[5m])) * 1000
```
Same as panel 2 but rendered as a single number with colour thresholds:
- Green: < 10ms (GPU inference territory)
- Yellow: 10–50ms (CPU inference, normal)
- Red: > 50ms (investigate — possible memory pressure, cold start, or overload)

**From our test:** 22.6ms → yellow → expected for CPU.

---

### Panel 6 — Error Rate
```promql
rate(classify_errors_total[5m])
```

**Plain English:** "How many classification errors per second?"

`ERRORS.inc()` is called in the `except` block of our FastAPI endpoint.
A flat line at 0 is the correct state. Any spike needs immediate investigation.

---

## Alternatives we considered

### Monitoring stack alternatives

| Option | Cost | Notes |
|--------|------|-------|
| **Grafana Cloud** (what we use) | Free tier | 10k series, 14-day retention, managed |
| **Local Prometheus + Grafana** | Free | Needs Docker for both; no cloud access |
| **Datadog** | $15+/host/month | Industry standard; excellent but expensive |
| **New Relic** | Free tier available | Good APM; heavier agent; vendor lock-in |
| **AWS CloudWatch** | Pay per metric | Good if already on AWS; vendor lock-in |
| **Victoria Metrics** | Free, open-source | Prometheus-compatible, more efficient; needs hosting |

**We chose Grafana Cloud** — same tooling as production Kubernetes setups,
free for our scale, no Docker required.

### Agent alternatives (instead of Grafana Alloy)

| Option | Notes |
|--------|-------|
| **Grafana Alloy** (what we use) | Recommended by Grafana Labs, replaces Grafana Agent |
| **Prometheus server** | Pull-based; stores locally; adds a layer we don't need |
| **OpenTelemetry Collector** | More general (traces + metrics + logs); more complex config |
| **Telegraf** | InfluxDB's agent; good but not Grafana-native |
| **Statsd** | Push model (app pushes to Statsd); different paradigm |

### Push vs Pull — a design decision worth understanding

**Prometheus is pull-based:** the collector comes to your service.
Your service doesn't need to know where the collector is. This makes
service discovery easier at scale (Kubernetes auto-discovers services).

**StatsD/OpenTelemetry is push-based:** your app actively sends metrics.
Works better when the service can't be reached (behind a strict firewall,
serverless functions with no persistent IP).

Alloy uses **remote_write** which is a push-based hybrid: it does a
Prometheus-style pull from localhost, then pushes to the cloud. Best of
both worlds for local development.

---

## Trade-offs we accepted

**15-second scrape interval**
Fine for monitoring. For real-time alerting (page someone within 30 seconds
of a failure), you'd drop to 5s — but free tier has data ingestion limits.

**No alerts configured**
Grafana supports alerts that fire to Slack, email, or PagerDuty when any
panel query crosses a threshold. For example: "alert if p95 > 100ms for
5 minutes." Not set up here — no on-call rotation for a portfolio project.
In production this is non-negotiable.

**14-day retention on free tier**
Long-term trend analysis requires more. Paid Grafana Cloud or self-hosted
Prometheus with configurable retention. 14 days is enough to demonstrate
the concept.

**Alloy only runs when your laptop is on**
Metrics stop flowing when the laptop sleeps. In production, FastAPI and
Alloy would run on a VM or Kubernetes pod, always-on. The `brew services start`
command makes Alloy auto-start on login, but it still can't run without power.

**API token in the config file**
The Alloy config at `/opt/homebrew/etc/alloy/config.alloy` contains the
Grafana API token. This file is NOT in the git repo (it's outside the project
directory). In production you'd inject secrets via environment variables or
a secrets manager (Vault, AWS Secrets Manager, Kubernetes Secrets).
Never commit API tokens to git.

---

## What this looks like in a production setup

At Glance/InMobi scale this pipeline would look like:

```
FastAPI pods (multiple replicas on Kubernetes)
        ↓
Prometheus (running in the cluster, scrapes all pods via service discovery)
        ↓
Grafana (running in the cluster or Grafana Cloud)
        ↓
Alertmanager → PagerDuty → on-call engineer
```

Key differences from our setup:
- Multiple FastAPI replicas → Prometheus aggregates across all of them
- Kubernetes service discovery → no hardcoded `localhost:8000`
- Alertmanager → automated paging, not just dashboards
- Longer retention → Thanos or Cortex for multi-month metric storage

Our setup is architecturally identical — just running locally instead
of in a cluster. The skills transfer directly.
