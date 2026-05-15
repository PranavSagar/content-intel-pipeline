# Grafana Monitoring — Metrics Dashboard

## What we built

A live dashboard in Grafana Cloud that visualises every request the FastAPI
classifier handles — in real time, auto-refreshing every 30 seconds.

```
FastAPI /metrics (localhost:8000)
        ↓  (scrape every 15s)
Grafana Alloy (local agent, installed via brew)
        ↓  (remote_write over HTTPS)
Grafana Cloud Prometheus (managed storage)
        ↓
Grafana Dashboard (6 panels, browser-accessible from anywhere)
```

---

## Why Grafana and not just print statements?

Print statements tell you what happened. Grafana shows you what's happening
and what's trending.

- **Print statement**: "classified → Sci/Tech (0.99) 18ms"
- **Grafana**: "p95 latency has been climbing from 20ms to 45ms over the last 2 hours"

One is a log entry. The other is an early warning system.

At Glance scale, the content enrichment service processes millions of events
per day. Nobody reads individual log lines at that volume — you watch
dashboards and set alerts.

---

## The metrics our FastAPI emits

From `src/serving/app.py`, three Prometheus metrics:

```python
REQUESTS = Counter("classify_requests", "...", ["label"])
LATENCY  = Histogram("classify_latency_seconds", "...")
ERRORS   = Counter("classify_errors", "...")
```

Prometheus automatically adds `_total` to counters, making them:
- `classify_requests_total{label="World"}` — count by label
- `classify_latency_seconds_bucket{le="..."}` — latency distribution buckets
- `classify_errors_total` — total errors

These are exposed at `GET /metrics` in Prometheus text format.

---

## What Grafana Alloy does

### Plain English
Think of Alloy as a postman that lives on your machine. Every 15 seconds
it knocks on FastAPI's door (`/metrics`), collects the numbers, and mails
them to Grafana Cloud. Grafana Cloud stores them with timestamps.

### Technical detail
Alloy is a telemetry collector (previously called Grafana Agent). It runs
as a background service (`brew services start grafana/grafana/alloy`) and
is configured with a `.alloy` file using HCL-like syntax.

Our config at `/opt/homebrew/etc/alloy/config.alloy`:
```hcl
prometheus.scrape "fastapi" {
  targets         = [{"__address__" = "localhost:8000"}]
  scrape_interval = "15s"
  metrics_path    = "/metrics"
  forward_to      = [prometheus.remote_write.metrics_hosted_prometheus.receiver]
}

prometheus.remote_write "metrics_hosted_prometheus" {
  endpoint {
    url = "https://prometheus-prod-43-prod-ap-south-1.grafana.net/api/prom/push"
    basic_auth {
      username = "<numeric-id>"
      password = "<api-token>"
    }
  }
}
```

`prometheus.scrape` → pulls metrics from FastAPI
`prometheus.remote_write` → pushes them to Grafana Cloud over HTTPS

---

## Why Grafana Cloud and not local Grafana?

| Option | Notes |
|--------|-------|
| **Local Grafana + Prometheus** | Needs Docker for both. User preference was managed cloud. |
| **Grafana Cloud** | Free tier (10k series, 14-day retention), browser-accessible, no Docker. |
| **Datadog** | Industry standard, but $15+/host/month. Overkill for a portfolio project. |
| **New Relic** | Similar to Datadog — powerful, expensive. |
| **Prometheus + Grafana self-hosted** | Right choice in production with Kubernetes, wrong choice for a local dev project. |

**We chose Grafana Cloud** — same dashboarding tool used in production, free
tier is genuinely useful, and the local Alloy agent bridges the gap between
localhost and the cloud.

---

## The 6 dashboard panels

### Panel 1 — Classification Rate (per minute, by label)
```promql
sum by (label) (rate(classify_requests_total[5m])) * 60
```
`rate()` computes per-second rate over the last 5 minutes.
Multiplying by 60 converts to per-minute (more readable).
`sum by (label)` splits the line by label — one line per class.

**What to watch**: sudden drop to 0 means the pipeline stopped sending.
Disproportionate spike in one label means the input distribution shifted.

### Panel 2 — Latency Percentiles (p50, p95, p99)
```promql
histogram_quantile(0.95, rate(classify_latency_seconds_bucket[5m])) * 1000
```
`histogram_quantile()` estimates the Nth percentile from bucket counts.
Multiplying by 1000 converts seconds → milliseconds.

**What to watch**: p95 climbing means some requests are getting slow.
p99 spiking while p50 is stable means occasional outliers (cold path,
GC pause, memory pressure) rather than a systemic slowdown.

**From our test**: p50 ~19ms, p95 ~23ms on CPU. Healthy.

### Panel 3 — Label Distribution (pie chart)
```promql
sum by (label) (increase(classify_requests_total[1h]))
```
`increase()` gives the total count over the window (not a rate).
Shows what proportion of articles fall into each class over the last hour.

**What to watch**: if one slice dominates (e.g. 80% Sci/Tech), the input
stream may have changed — or the model may be misclassifying.
This is the visual version of what Evidently checks statistically.

### Panel 4 — Total Requests (stat)
```promql
sum(classify_requests_total)
```
Simple cumulative count since the server started. Quick sanity check.

### Panel 5 — p95 Latency stat
```promql
histogram_quantile(0.95, rate(classify_latency_seconds_bucket[5m])) * 1000
```
Same query as panel 2 but as a single number with colour thresholds:
- Green: < 10ms
- Yellow: 10–50ms
- Red: > 50ms

**From our test**: 22.6ms → yellow. Acceptable for CPU inference.
On GPU this would be green (< 5ms).

### Panel 6 — Error Rate
```promql
rate(classify_errors_total[5m])
```
Flat at 0 means no errors. Any spike here needs immediate investigation.

---

## What the dashboard looked like after our first test

From our test run (30 curl requests across 3 article types):
- Total requests: 44
- p95 latency: 22.6ms
- Label distribution: 100% Sci/Tech (all 3 test articles classified as Sci/Tech)
- Error rate: 0

Running the full pipeline (producer + consumer) populates all 4 labels
and shows a more realistic distribution.

---

## Trade-offs accepted

**15-second scrape interval**
Fine for a monitoring use case. For real-time alerting (page someone within
30 seconds of a problem), you'd drop to 5s. Free tier has limits on data
ingestion rate.

**No alerting configured**
Grafana supports alerts (email, Slack, PagerDuty) on any panel query.
For example: "alert if p95 > 100ms for 5 minutes". Not set up here —
this is a portfolio project, not an on-call rotation.

**14-day data retention on free tier**
For long-term trend analysis you'd need a paid tier or a self-hosted
Prometheus with longer retention. For demo purposes, 14 days is enough
to show the concept.

**Alloy only runs when your laptop is on**
In production, the FastAPI server and Alloy would run on a cloud VM or
Kubernetes pod, always-on. Locally, if your laptop sleeps, metrics stop
flowing. No data gap handling — Grafana just shows a gap in the chart.
