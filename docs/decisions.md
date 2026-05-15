# Architecture Decisions

A log of the major architectural choices made while building this project,
in the lightweight [ADR](https://adr.github.io/) format. Each entry
captures the *context*, the *decision*, and the *consequences* —
including trade-offs we knowingly accepted.

This is the **why** behind the design. The **what** lives in
[`architecture.md`](architecture.md). The **how each problem was solved
in practice** lives in [`build-log.md`](build-log.md).

> **How to extend this document.** When a new architectural decision is
> made, add a numbered ADR at the end. Never renumber existing ones —
> if a decision is later reversed, mark its status as *Superseded by
> ADR-N* and add the new one. ADRs are append-only; that's what makes
> them useful as a history.

---

## ADR-001 — DistilBERT over BERT

**Status:** Accepted

**Context:** The task is 4-class news classification on AG News. BERT-base
(110M parameters) is the strong default for transformer-based text
classification. We have a target latency budget of p95 < 25ms on CPU
inference and a training budget of "one overnight run on a Mac".

**Decision:** Use `distilbert-base-uncased` (66M parameters).

**Consequences:**
- 94.64% test accuracy — within ~1.5% of BERT-base on this 4-class task.
- ~2× faster inference and ~40% smaller memory footprint.
- Trains in ~82 minutes on Apple Silicon MPS vs ~3 hours for BERT.
- We lose accuracy headroom for harder tasks (10+ classes, fine-grained
  sentiment). Acceptable here; would revisit for a different domain.

---

## ADR-002 — Asynchronous pipeline (Kafka) over direct API calls

**Status:** Accepted

**Context:** Articles arrive as a continuous stream. The simplest design
is for the producer to call `POST /classify` directly. The alternative
is to push articles into a message broker that a consumer reads from.

**Decision:** Use a Kafka topic (Redpanda Cloud) between producer and
classifier.

**Consequences:**
- Producer is decoupled from classifier latency — it can produce at its
  own rate without backpressure.
- Classifier can restart, deploy a new version, or be temporarily slow
  without dropping articles. Messages queue in the topic.
- Enables **replay**: rewind the topic offset and re-classify with a
  new model version. This is the key MLOps feature.
- Cost: more moving parts (broker, consumer group, offset management),
  more secrets to manage, an extra failure mode (broker unreachable).

---

## ADR-003 — Redpanda Cloud over Confluent / self-hosted Kafka

**Status:** Accepted

**Context:** ADR-002 commits us to a Kafka-compatible broker. Options
evaluated:

| Option | Verdict |
|---|---|
| Confluent Cloud | Best production-grade option; paid plans start at $400+/mo |
| Apache Kafka self-hosted | Needs Docker or JVM; operational overhead |
| Upstash Kafka | Was the initial plan; **deprecated** by Upstash mid-project |
| Redpanda Cloud | Free tier, no credit card, Kafka wire-compatible, no JVM |

**Decision:** Redpanda Cloud free tier.

**Consequences:**
- Same `confluent-kafka` Python library, same topics/consumer-groups
  semantics. Migration to Confluent or self-hosted is a one-line URL
  change.
- No JVM = no Zookeeper, no JMX tuning, no GC pauses to worry about.
- Free tier limits (partitions, retention) are acceptable for portfolio
  scale; would need a paid plan in production.

---

## ADR-004 — Manual offset commit (at-least-once delivery)

**Status:** Accepted

**Context:** Kafka consumers can commit offsets automatically (after
poll, before processing) or manually (after the work is durably done).
Auto-commit is simpler but allows silent data loss: if the consumer
crashes after commit but before the DB write, the message is gone.

**Decision:** `enable.auto.commit=False`. Commit explicitly only after
the SQLite INSERT succeeds.

**Consequences:**
- At-least-once delivery guarantee. On crash, the un-committed message
  is re-polled when the consumer restarts.
- Duplicates are possible but harmless: identical text hits the Redis
  cache and returns the same result instantly, written as a separate
  row with the same `text_hash`.
- Silent data loss is now impossible — failures are loud (re-processing)
  instead of quiet (missing data).

---

## ADR-005 — Redis cache with SHA-256 keys, 1-hour TTL

**Status:** Accepted

**Context:** News wire services (Reuters, AP, AFP) distribute identical
articles to many outlets. The same headline appears repeatedly on the
stream. Without dedup, each duplicate costs a full inference (~20ms).

**Decision:** Redis (Upstash managed) with `classify:<sha256(text)>` as
the key, the full result JSON as the value, TTL = 3600 seconds.

**Consequences:**
- Cache hit returns in <1ms vs 20ms inference — 20× speedup on duplicates.
- SHA-256 hex digest as key: bounded size (64 chars), no Redis key
  parsing surprises with article punctuation/quotes.
- 1-hour TTL gives a safe window: after a new model deploys, every
  cached prediction expires within an hour without manual cache
  invalidation.
- Trade-off: TTL means we re-classify identical articles every hour
  even when the model hasn't changed. Acceptable cost.

---

## ADR-006 — SQLite for classification storage

**Status:** Accepted

**Context:** Every classification needs to be persisted for the drift
monitor to read. The choice is between embedded (SQLite), self-hosted
(Postgres in Docker), or managed (Supabase, Neon, Cloud SQL).

**Decision:** SQLite file (`classifications.db`).

**Consequences:**
- Zero setup. Ships with the Python standard library. No connection
  pooling, no credentials, no separate process to manage.
- Single-writer is fine: only one consumer process writes today.
- Will hit a wall at multiple concurrent writers (consumer scale-out).
  Migration target is managed Postgres; the table schema is simple
  enough that the migration is a one-day task.
- Read performance is excellent for the drift query (`SELECT WHERE
  created_at > ...`) — a single index on `created_at` is enough.

---

## ADR-007 — Evidently for drift detection

**Status:** Accepted

**Context:** Need to detect when the model's live-traffic distribution
drifts from the training distribution — without manually eyeballing
charts.

**Decision:** Evidently (statistical drift tests + HTML reports), run
on a weekly schedule.

**Consequences:**
- Zero infrastructure — runs as a Python script. Generates HTML
  reports and logs metrics to MLflow.
- Statistical tests (Jensen-Shannon for categorical labels, Wasserstein
  for numeric confidence) are more reliable than threshold-based rules.
- Evidently chose its 0.7.x release window to break every import path
  and rename `Report.run()` to return a Snapshot — see build-log #7.
  Version pinning matters.
- Alternative considered: WhyLabs (managed, paid). Overkill at this
  scale.

---

## ADR-008 — HuggingFace Spaces (Docker) for production hosting

**Status:** Accepted

**Context:** Need to host the FastAPI server publicly with HTTPS, a
stable URL, and zero ongoing cost.

**Decision:** HuggingFace Spaces with the Docker SDK.

**Consequences:**
- Free CPU hosting with HTTPS at a stable `*.hf.space` URL.
- Git-based deploy: `git push` to the Space repo triggers a Docker
  rebuild. Our CD workflow automates this from GitHub Actions.
- Limited to CPU on the free tier. For GPU we'd move to HF Inference
  Endpoints (paid) or a cloud GPU instance.
- Free Space sleeps after inactivity — first call after sleep has a
  ~5s cold start. Acceptable for a portfolio demo.

---

## ADR-009 — Grafana Cloud with Alloy bridge (pull + push hybrid)

**Status:** Accepted

**Context:** FastAPI exposes Prometheus metrics on `/metrics` (pull
model). Grafana Cloud's managed Prometheus cannot reach into our
process to scrape — it lives outside our network. Standard Prometheus
is pull-only; managed services need push.

**Decision:** Run Grafana Alloy as a local agent. Alloy pulls `/metrics`
from FastAPI every 15s (standard Prometheus scrape) and pushes
samples to Grafana Cloud Prometheus via `remote_write` (push).

**Consequences:**
- Free managed Prometheus + Grafana Cloud with no Prometheus server to
  maintain ourselves.
- FastAPI code is unchanged — it just exposes `/metrics` like any
  other Prometheus client.
- Alloy is the single point of failure for ingestion. Acceptable
  because in-process counters keep accumulating during Alloy outages;
  the next scrape fills the gap.
- Alloy runs on the dev laptop today. On Kubernetes, it gets replaced
  by a Prometheus operator side-car — FastAPI side stays the same.

---

## ADR-010 — Static UI on GitHub Pages, separate origin from API

**Status:** Accepted

**Context:** Need a user-facing demo UI. Options were:

1. Embed an HTML page inside FastAPI (same origin, no CORS).
2. Build a Gradio Space (Gradio SDK on HuggingFace, calls our API).
3. Static HTML/JS on GitHub Pages calling the HF Spaces API.

**Decision:** Option 3 — static page at `pranavsagar.github.io/classify/`.

**Consequences:**
- UI and API deploy and version independently. Pushing UI changes
  doesn't redeploy the API container.
- `git push` is the deploy. No build step, no dependencies.
- Required adding `CORSMiddleware` to FastAPI — UI on `github.io`
  origin, API on `hf.space` origin (build-log #15).
- One extra DNS hop and TLS handshake from the user's perspective. The
  HTML loads from GitHub Pages, then fetches from HF Spaces.

---

## ADR-011 — Mermaid for every architecture diagram

**Status:** Accepted

**Context:** Architecture docs need diagrams. The default reach for
diagrams is a PNG/SVG exported from Excalidraw, Lucidchart, or
draw.io. These rot: the source file is rarely committed, can't be
diffed, and goes out of sync with the code.

**Decision:** Mermaid syntax inside fenced code blocks in markdown.

**Consequences:**
- GitHub renders diagrams natively in markdown previews and PRs.
- Diagrams are plain text — diffable, reviewable, editable in any
  text editor without specialised software.
- Anyone who can write markdown can update a diagram.
- Mermaid's auto-layout is less flexible than draw.io for very
  complex diagrams. Acceptable trade-off — when a diagram gets that
  complex, it usually means it should be split into two.

---

## ADR-012 — Synthetic drift reference (known limitation)

**Status:** Accepted, with planned remediation in roadmap

**Context:** Evidently drift detection requires a *reference*
distribution to compare live traffic against. The proper way to build
this reference is to run the full AG News test set (7,600 articles)
through the live API and persist `(label, confidence)` pairs. That run
takes ~2 hours at 1 msg/sec.

**Decision:** For the first iteration, synthesise the reference:
1,900 rows per label (uniform 25%) and confidence drawn from
`Normal(0.97, 0.05)` clipped to [0.5, 1.0].

**Consequences:**
- Drift monitoring is functional from day one — no two-hour baseline
  run required to bootstrap.
- **False positive** on confidence drift: real DistilBERT confidence
  is bimodal (very confident or genuinely uncertain), but the
  synthetic reference is a smooth bell curve. Wasserstein distance
  picks up the shape mismatch. See build-log #9.
- The drift on `label` distribution is still accurate — categorical
  drift detection works against synthetic uniform reference.
- **Roadmap fix**: replace with a real baseline run, persist it as a
  versioned artifact alongside the model.
