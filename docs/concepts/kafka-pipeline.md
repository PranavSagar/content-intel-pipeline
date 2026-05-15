# The Kafka Pipeline — Producer + Consumer

## What we built

A real-time pipeline that simulates a content stream and classifies
every article as it arrives.

```
AG News articles
      ↓
  Producer → [content-stream topic] → Consumer → POST /classify → Redis cache
                                                                → SQLite DB
```

---

## Why Kafka here at all?

You could call `/classify` directly from wherever content comes from.
So why add Kafka in the middle?

### Plain English
Think of a busy restaurant during lunch rush.

Without Kafka: Every customer walks directly into the kitchen and demands food.
If the chef is slow, customers pile up at the kitchen door, blocking each other.
If the kitchen goes down, orders are lost.

With Kafka: Customers place orders at the counter (producer). Orders go into a
ticket rail (topic). The kitchen (consumer) processes tickets at its own pace.
If the kitchen slows down, tickets queue on the rail. If it goes down, tickets
wait — nothing is lost. The counter doesn't care how fast the kitchen is.

### Technical reasons
1. **Decoupling** — producer and consumer are independent processes. You can
   restart, redeploy, or scale the consumer without touching the producer.
2. **Backpressure** — if the classifier is slow, messages queue in the topic
   instead of being dropped or blocking the source.
3. **Replayability** — Kafka retains messages. You can rewind the topic and
   reprocess historical articles through a new model version. Critical for
   drift monitoring — you compare old vs new model on the same data.
4. **At Glance scale** — you've seen this firsthand. The content enrichment
   service reads from Kafka because no upstream publisher should block waiting
   for enrichment to finish.

---

## Why Redpanda and not Apache Kafka?

Apache Kafka runs on the JVM — it needs a Java process just to start,
uses significant memory, and historically required ZooKeeper as a coordination
service (a separate process to manage).

Redpanda is written in C++:
- Starts in seconds (no JVM warmup)
- Uses a fraction of the memory
- No ZooKeeper dependency
- Wire-compatible with the Kafka protocol — same `confluent-kafka` Python library,
  same API, same concepts

**Why it matters**: We use the exact same code we'd use with Apache Kafka or
Confluent Cloud. Redpanda just happens to be the broker. Swapping brokers
requires changing only the bootstrap server URL, nothing else.

### Alternatives we considered

| Option | Why we didn't use it |
|--------|---------------------|
| **Apache Kafka (self-hosted)** | Needs Docker or a JVM install. User preference was managed cloud services, not local containers. |
| **Confluent Cloud** | Best production option, but the free tier is limited and paid plans start at $400+/month. |
| **Upstash Kafka** | Was our first choice (same company as our Redis). They deprecated their Kafka product — we discovered this mid-build. |
| **RabbitMQ** | A message queue, not a log. No replayability — once consumed, a message is gone. Doesn't fit drift monitoring. |
| **AWS SQS / GCP Pub/Sub** | Cloud-vendor-locked. Good in production but adds friction for a local-first portfolio project. |

**We chose Redpanda Cloud** — free tier, no credit card, no Docker, Kafka-compatible wire protocol.

---

## What ACLs are and why we set them

### Plain English
When you created `content-intel-user`, it had an identity but no permissions —
like a new employee with a badge but no door access.

ACLs (Access Control Lists) are the door access rules.
We gave `content-intel-user`:
- Access to topic `content-stream` — so it can produce and consume
- Access to consumer groups — so the consumer can track its position in the topic

Without ACLs, the broker would accept the connection (identity verified)
but reject every produce/consume operation (no permission).

### Technical detail
Redpanda uses SASL-SCRAM authentication — the same mechanism used by
Confluent Cloud and enterprise Kafka clusters.

```
SASL = Simple Authentication and Security Layer  (the framework)
SCRAM = Salted Challenge Response Authentication  (the method)
SHA-256 = the hash algorithm used
```

The connection handshake:
1. Client connects with username + password
2. Broker verifies identity (SASL-SCRAM)
3. Broker checks ACLs for the requested operation
4. If allowed: operation proceeds. If denied: `TopicAuthorizationException`

Our Python config that wires this up:
```python
{
    'bootstrap.servers': 'seed.redpanda.com:9092',
    'security.protocol': 'SASL_SSL',   # encrypted + authenticated
    'sasl.mechanism': 'SCRAM-SHA-256',
    'sasl.username': 'content-intel-user',
    'sasl.password': 'your-password',
}
```

---

## The Producer

### Plain English
The producer simulates a live content stream by pulling articles from the
AG News test set and sending them to the Kafka topic at a set rate.

Think of it like a firehose — it doesn't care who reads the messages,
it just keeps sending. In a real system, this would be replaced by actual
content ingestion (web scraper, CMS webhook, RSS feed).

### What it does
1. Loads AG News test articles (7,600 articles — our "content stream")
2. Picks a random article every N seconds
3. Wraps it in a JSON payload with a timestamp
4. Sends it to the `content-stream` topic

```python
payload = {
    "text": "NASA launches new telescope...",
    "sent_at": 1747123456.789   # unix timestamp, used to measure pipeline lag
}
```

### Key decision — rate limiting
We send 1 message/second by default. Why not faster?
- Free tier limits: Redpanda Serverless has throughput limits
- For a demo, 1/sec is enough to show the pipeline working
- In production at Glance scale, you'd have thousands/sec but with a
  production-grade cluster, not a free tier

### Key decision — async delivery with callback

```python
producer.produce(topic, value=payload, callback=on_delivery)
producer.poll(0)
```

`produce()` doesn't wait for the broker to confirm. It queues the message
internally and returns immediately. This is intentional — blocking on every
send would cap throughput at one round-trip per message.

`poll(0)` tells the producer to check for delivery confirmations without
blocking. Without it, callbacks never fire and the internal queue fills up
until the process crashes.

`producer.flush()` in the `finally` block is non-negotiable: it blocks until
every queued message is either delivered or has permanently failed. Skip it
and any messages still in the buffer vanish when the process exits.

**Alternative**: synchronous sending (block until confirmed per message).
Simpler to reason about, but throughput is limited to 1 msg per round-trip.
Fine for our 1 msg/sec rate, but wrong at scale.

---

## The Consumer

### Plain English
The consumer is the other end of the pipeline. It continuously reads messages
from the `content-stream` topic and for each one:
1. Checks Redis cache — "have we classified this exact text before?"
2. If cached: returns the cached result (fast, no API call)
3. If not cached: calls POST /classify, gets the prediction, stores in cache
4. Saves the result to SQLite for monitoring later

Think of the consumer like a quality checker on a factory line. Each item
(article) passes through the checker. If the item has already been stamped
(cached), the checker just logs it. If not, it sends the item to the lab
(classifier), gets the result, stamps it for next time, and logs it.

### Why Redis cache here?
News articles repeat. Wire services distribute the same story to hundreds of
outlets with minor edits. The same headline can appear many times in a stream.

Without cache: every duplicate calls the model — wasted compute, higher latency.
With cache: second occurrence returns in <1ms from Redis instead of 20ms from model.

The cache key is a hash of the text. TTL (time to live) is 1 hour — after that,
we re-classify in case the model has been updated.

### Consumer groups — what they are
A consumer group is how Kafka tracks "how far has this consumer read?"

Each message in a topic has an offset (like a line number). The consumer group
stores the last offset processed. If the consumer crashes and restarts, it picks
up from where it left off — no messages skipped, no messages reprocessed.

```
Topic: content-stream
Offset: 0    1    2    3    4    5    6 ...
              ↑
         consumer group "content-intel-consumer"
         last processed = offset 1
         → next read starts at offset 2
```

Without consumer groups, every restart would reprocess from the beginning
or miss everything that arrived during downtime.

---

## Deep dive: Consumer implementation decisions

These are the decisions inside `consumer.py` that aren't obvious from the
high-level description above.

---

### Decision 1 — Manual commit, not auto-commit

**What we did:**
```python
"enable.auto.commit": False
# ... process the message ...
consumer.commit(message=msg)   # only after successful processing
```

**Plain English:**
Auto-commit is like a student who marks homework "done" the moment they pick
it up from the pile — before actually doing it. If they drop the homework
on the way to their desk, it's gone but marked complete.

Manual commit is marking "done" only after you've actually finished and
submitted the work. If something goes wrong mid-way, you pick it up again.

**Technical detail:**
With `enable.auto.commit: True`, Kafka periodically commits the offset of
the last polled message — regardless of whether your processing succeeded.
If your process crashes after auto-commit but before writing to SQLite:
- Kafka thinks the message is done (offset committed)
- The message is never written to your DB
- Silent data loss

With manual commit:
- You process the message fully (Redis + SQLite + everything)
- Then call `consumer.commit(message=msg)`
- If you crash before commit, Kafka replays the message on restart
- Worst case: duplicate processing. You handle that with Redis (idempotent cache write)

**Trade-off:**
Manual commit gives you "at least once" delivery — messages may be processed
more than once (on crash + replay), but never silently dropped.
Auto-commit risks "at most once" — messages can be silently skipped.

For classification: a duplicate classification is harmless. A silent skip
(article never classified, never in SQLite) is a data gap you won't notice
until drift monitoring is wrong.

**Alternatives:**
- "Exactly once" delivery: Kafka supports it (transactional API), but requires
  both the producer and consumer to use Kafka transactions, and your downstream
  systems (Redis, SQLite) to participate. Significant complexity. Not worth it
  here — Redis writes are idempotent anyway.

---

### Decision 2 — `auto.offset.reset: earliest`

**What we did:**
```python
"auto.offset.reset": "earliest"
```

**Plain English:**
Imagine you're joining a team Slack channel for the first time. Do you read
from the very beginning of the channel history, or do you start from right now?

`earliest` = read from the beginning (first time only)
`latest` = start from right now, ignore everything before you joined

**Technical detail:**
This setting only matters when a consumer group has no committed offset yet
(i.e., the very first time you run). After the first run, the committed offset
takes over and this setting is ignored.

We use `earliest` so that if you start the consumer *after* the producer has
already sent some messages, you don't miss them. In production you'd often
use `latest` — you don't want a new deployment reprocessing months of history.

**Trade-off:**
`earliest` on first run reprocesses all historical messages. For our 7,600-article
demo that's fine. For a production topic with 6 months of data, you'd use `latest`
or manually set the offset.

---

### Decision 3 — SHA-256 hash as the Redis cache key

**What we did:**
```python
def make_cache_key(text: str) -> str:
    return "classify:" + hashlib.sha256(text.encode()).hexdigest()
```

**Plain English:**
We need a Redis key that uniquely identifies an article. We could use the
text itself as the key, but Redis keys can be at most 512MB and long keys
are slow to compare. Instead, we hash the text: any article produces a
fixed-length 64-character fingerprint.

Two different articles will almost certainly produce different hashes.
Same article always produces the same hash.

**Technical detail:**
SHA-256 produces a 256-bit (64 hex character) digest. The probability of
two different texts producing the same hash (collision) is 1 in 2^256 —
effectively impossible.

The `"classify:"` prefix is a Redis naming convention. Redis is a flat
key-value store with no namespaces. Prefixes act as namespaces: if you
later add other keys (e.g. `"model:"`, `"session:"`), they don't collide
with your classification cache.

**Alternatives:**
| Approach | Problem |
|----------|---------|
| Raw text as key | Long keys are slow; key size limit is 512MB |
| MD5 hash | Faster but cryptographically broken (collisions known). Fine for cache keys (not security), but SHA-256 is habit worth building |
| UUID | Random — same article would get a different key each time, defeating the cache |
| First 100 chars of text | Two articles with the same opening would share a cache entry — wrong result served |

---

### Decision 4 — Cache TTL of 1 hour

**What we did:**
```python
CACHE_TTL_SECONDS = 3600  # 1 hour
cache.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))
```

**Plain English:**
TTL (Time To Live) means: after 1 hour, Redis automatically deletes this
cached result. Next time the same article arrives, it gets re-classified.

Why not cache forever?
- If you update your model (retrain, deploy new version), stale cache entries
  would return results from the old model. You'd never see the improvement.
- Memory: Redis on the free tier has limits. Expiring entries frees space automatically.

Why 1 hour and not 24 hours or 5 minutes?
- 1 hour: long enough to catch repeating breaking news within the same news cycle
- Short enough that a model redeploy takes effect within the hour
- Arbitrary — production systems tune this based on how often the model updates

**Trade-off:**
Longer TTL = more cache hits, less compute, but stale results after model update.
Shorter TTL = fresher results, more model calls, higher latency and cost.

---

### Decision 5 — `httpx` and not `requests`

**What we did:**
```python
import httpx
with httpx.Client() as http:
    response = http.post(CLASSIFY_URL, json={"text": text})
```

**Plain English:**
Both `requests` and `httpx` are HTTP client libraries for Python. We use `httpx`
because it supports both sync and async modes from the same API.

**Technical detail:**
`requests` is synchronous only. `httpx` has an identical sync API but also
offers `httpx.AsyncClient` for async code — same methods, same interface.

If you later make the consumer async (to process multiple articles concurrently
without waiting for each classify call), you swap `httpx.Client` for
`httpx.AsyncClient` and add `await` — nothing else changes.

With `requests`, switching to async would require rewriting to `aiohttp` with
a completely different API.

We also use `httpx.Client()` as a context manager (`with` block), which keeps
a connection pool open for the lifetime of the consumer. Without this, every
classify call opens and closes a new TCP connection — adding ~50ms of overhead
each time.

**Alternatives:**
| Library | Notes |
|---------|-------|
| `requests` | Most popular, sync only, no path to async |
| `aiohttp` | Async-first but verbose, different API from requests |
| `urllib3` (stdlib) | Low-level, no JSON helpers, tedious to use |

---

### Decision 6 — Manual offset commit per message vs. batch commit

**What we did:**
```python
consumer.commit(message=msg)   # commit after every single message
```

**Alternative we could have used:**
```python
# Commit every N messages or every N seconds
if processed % 100 == 0:
    consumer.commit()
```

**Plain English:**
Committing after every message is the safest option. If you crash after
message 47 and you committed up to 47, you replay only from 48.

Batch commit is faster (fewer network calls to the broker) but widens the
replay window. If you batch every 100 messages and crash at 147 with a
committed offset of 100, you reprocess 47 messages.

**We chose per-message commit** because at 1 msg/sec our throughput is low
and the safety matters more than the minimal overhead. At high throughput
(thousands/sec), batch commit is the right choice.

---

### Decision 7 — `consumer.close()` vs just exiting

**What we did:**
```python
finally:
    consumer.close()
```

**Plain English:**
When a consumer leaves a group, it sends a "LeaveGroup" message to Kafka.
Kafka then immediately reassigns this consumer's partitions to other active
consumers.

Without `close()`, Kafka doesn't know you've left — it waits for the session
timeout (typically 30-45 seconds) before assuming you're dead and reassigning.
During those 30-45 seconds, no other consumer can read from your partition.

For our single-consumer setup this doesn't matter (there's nobody to reassign
to), but it's the correct pattern and matters the moment you scale to multiple
consumers.

---

### Decision 8 — `datasets` not included in `requirements-pipeline.txt`

The producer loads AG News via `load_dataset("ag_news")` which needs the
HuggingFace `datasets` library. But `datasets` is already in
`requirements-training.txt` — it was installed when you set up training.

We didn't add it to `requirements-pipeline.txt` to avoid double-specifying it.
In a production setup you'd have a unified `requirements.txt` or a proper
dependency manager (Poetry, uv) that handles this without duplication.

---

## The full flow for one message

```
Producer picks article: "Apple announces new iPhone model"
        ↓
Wraps in JSON + timestamp (sent_at = unix time)
        ↓
Sends to topic: content-stream (offset 47)
        ↓
Consumer reads offset 47
        ↓
Computes cache key: sha256("Apple announces new iPhone model") → "classify:ab3f..."
        ↓
Checks Redis: cache miss (first time seeing this)
        ↓
POST /classify {"text": "Apple announces new iPhone..."}
        ↓
FastAPI → DistilBERT → {"label": "Sci/Tech", "confidence": 0.94, "latency_ms": 8.2}
        ↓
Stores in Redis: key → result, TTL 1 hour
        ↓
Saves to SQLite: text | label | confidence | latency_ms | cached=False | timestamp
        ↓
consumer.commit(message=msg) → group advances offset to 48
```

If the same article appears again at offset 112:
```
Consumer reads offset 112
        ↓
Cache hit in Redis → returns instantly (<1ms)
        ↓
Saves to SQLite: cached=True, latency_ms ≈ 0
        ↓
consumer.commit(message=msg)
```

---

## What SQLite stores and why

We store every classification result in SQLite locally. This becomes the
data source for drift monitoring later — we can look at how the label
distribution changes over time, or how confidence scores trend.

Schema:
```sql
CREATE TABLE IF NOT EXISTS classifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    label       TEXT NOT NULL,
    confidence  REAL NOT NULL,
    latency_ms  REAL,
    cached      BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Why `CREATE TABLE IF NOT EXISTS`?**
The consumer creates this table on startup. If you restart the consumer,
it runs the same CREATE statement — without `IF NOT EXISTS` it would crash
("table already exists"). With it, the statement is idempotent: safe to run
multiple times.

**Why store `cached`?**
When you run drift monitoring later, you want to analyze real model predictions,
not cache replays. Filtering `WHERE cached = FALSE` gives you only the rows
where DistilBERT actually ran.

**Why store `latency_ms`?**
It lets you plot model latency over time in Grafana. If latency suddenly
spikes, something changed — new model version, memory pressure, cold start.
Cached rows get `latency_ms ≈ 0`, which visually separates them in dashboards.

---

## Trade-offs we accepted

**SQLite not PostgreSQL**
For local dev, SQLite is zero-setup. Same schema, same queries — swapping
to Neon PostgreSQL later only requires changing the connection string and
driver (`psycopg2` instead of `sqlite3`). The `classifications` table
schema is already designed to be Postgres-compatible (standard SQL, no
SQLite-specific types).

**No schema registry**
In production Kafka setups, message schemas are registered centrally so
producers and consumers agree on the format. We use raw JSON — simpler,
good enough for a portfolio project. The downside: if someone changes the
producer payload format, the consumer silently breaks. A schema registry
(Confluent Schema Registry, Redpanda Schema Registry) would catch this at
publish time.

**Single partition**
Our topic has 1 partition, so 1 consumer can process it. Multiple partitions
allow multiple consumers in parallel (horizontal scaling). For our data rate
(1 msg/sec), one partition is more than enough. The code is already
consumer-group-aware — adding partitions and consumers requires only
Redpanda config changes, not code changes.

**At least once, not exactly once**
Manual commit gives us at-least-once delivery. A crash between processing
and commit causes one message to be reprocessed. We accept this because:
- Redis writes are idempotent (writing the same key twice is harmless)
- SQLite gets a duplicate row in the rare crash-replay scenario — acceptable
  for a monitoring dataset

Exactly-once would require Kafka transactions + a transactional database
(PostgreSQL with proper transaction support). Significant complexity for
a problem we're unlikely to hit at 1 msg/sec.

**`CLASSIFY_URL` hardcoded to localhost**
The consumer assumes the FastAPI server is running locally. In production,
this would be a service discovery URL or a Kubernetes internal DNS name.
We expose it as an environment variable (`CLASSIFY_URL`) so it's easy to
override without touching code.
