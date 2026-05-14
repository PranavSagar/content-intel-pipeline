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
    "timestamp": 1747123456.789
}
```

### Key decision — rate limiting
We send 1 message/second by default. Why not faster?
- Free tier limits: Redpanda Serverless has throughput limits
- For a demo, 1/sec is enough to show the pipeline working
- In production at Glance scale, you'd have thousands/sec but with a
  production-grade cluster, not a free tier

---

## The Consumer

### Plain English
The consumer is the other end of the pipeline. It continuously reads messages
from the `content-stream` topic and for each one:
1. Checks Redis cache — "have we classified this exact text before?"
2. If cached: returns the cached result (fast, no API call)
3. If not cached: calls POST /classify, gets the prediction, stores in cache
4. Saves the result to SQLite for monitoring later

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

## The full flow for one message

```
Producer picks article: "Apple announces new iPhone model"
        ↓
Wraps in JSON + timestamp
        ↓
Sends to topic: content-stream (offset 47)
        ↓
Consumer reads offset 47
        ↓
Computes cache key: hash("Apple announces new iPhone model")
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
Consumer commits offset 47 → group advances to 48
```

If the same article appears again at offset 112:
```
Consumer reads offset 112
        ↓
Cache hit in Redis → returns instantly
        ↓
Saves to SQLite: cached=True, latency_ms ≈ 0
        ↓
Consumer commits offset 112
```

---

## What SQLite stores and why

We store every classification result in SQLite locally. This becomes the
data source for drift monitoring later — we can look at how the label
distribution changes over time, or how confidence scores trend.

Schema:
```sql
CREATE TABLE classifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    label       TEXT NOT NULL,
    confidence  REAL NOT NULL,
    latency_ms  REAL,
    cached      BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

In production this would be PostgreSQL (Neon). SQLite works for local dev
because it's zero-setup — just a file on disk.

---

## Trade-offs we accepted

**SQLite not PostgreSQL**
For local dev, SQLite is zero-setup. Same schema, same queries — swapping
to PostgreSQL later only requires changing the connection string.

**No schema registry**
In production Kafka setups, message schemas are registered centrally so
producers and consumers agree on the format. We use raw JSON — simpler,
good enough for a portfolio project.

**Single partition**
Our topic has 1 partition, so 1 consumer can process it. Multiple partitions
allow multiple consumers in parallel (horizontal scaling). For our data rate
(1 msg/sec), one partition is more than enough.
