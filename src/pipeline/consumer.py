import hashlib
import json
import os
import sqlite3
import time

import httpx
import redis
from confluent_kafka import Consumer, KafkaError
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

CLASSIFY_URL = os.environ.get("CLASSIFY_URL", "http://localhost:8000/classify")
CACHE_TTL_SECONDS = 3600  # 1 hour


def make_kafka_config() -> dict:
    return {
        "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": os.environ["KAFKA_USERNAME"],
        "sasl.password": os.environ["KAFKA_PASSWORD"],
        "group.id": "content-intel-consumer",
        # earliest: on first run, start from the beginning of the topic.
        # If the group already has a committed offset, that takes precedence.
        "auto.offset.reset": "earliest",
        # We commit manually after processing so a crash before commit
        # causes the message to be reprocessed, not silently skipped.
        "enable.auto.commit": False,
    }


def make_cache_key(text: str) -> str:
    return "classify:" + hashlib.sha256(text.encode()).hexdigest()


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS classifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            text        TEXT NOT NULL,
            label       TEXT NOT NULL,
            confidence  REAL NOT NULL,
            latency_ms  REAL,
            cached      BOOLEAN DEFAULT FALSE,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def classify_via_api(client: httpx.Client, text: str) -> dict:
    response = client.post(CLASSIFY_URL, json={"text": text}, timeout=10.0)
    response.raise_for_status()
    return response.json()


def process_message(
    text: str,
    cache: redis.Redis,
    db: sqlite3.Connection,
    http: httpx.Client,
):
    cache_key = make_cache_key(text)
    cached_raw = cache.get(cache_key)

    if cached_raw:
        result = json.loads(cached_raw)
        cached = True
        print(f"[consumer] cache hit  → {result['label']} ({result['confidence']:.2f})")
    else:
        result = classify_via_api(http, text)
        cache.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(result))
        cached = False
        print(
            f"[consumer] classified → {result['label']} ({result['confidence']:.2f})"
            f"  {result['latency_ms']:.1f}ms"
        )

    db.execute(
        """
        INSERT INTO classifications (text, label, confidence, latency_ms, cached)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            text,
            result["label"],
            result["confidence"],
            result.get("latency_ms"),
            cached,
        ),
    )
    db.commit()


def run():
    redis_client = redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    db_conn = sqlite3.connect("classifications.db")
    init_db(db_conn)

    consumer = Consumer(make_kafka_config())
    topic = os.environ["KAFKA_TOPIC"]
    consumer.subscribe([topic])

    print(f"[consumer] subscribed to '{topic}', waiting for messages...")
    print("[consumer] press Ctrl+C to stop\n")

    processed = 0
    with httpx.Client() as http:
        try:
            while True:
                # poll() blocks for up to 1 second waiting for a message.
                # Returns None on timeout — that's normal, just keep looping.
                msg = consumer.poll(timeout=1.0)
                if msg is None:
                    continue

                if msg.error():
                    # PARTITION_EOF is informational (caught up to end of partition),
                    # not a real error — skip it silently.
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    print(f"[consumer] kafka error: {msg.error()}")
                    continue

                payload = json.loads(msg.value().decode("utf-8"))
                text = payload["text"]
                sent_at = payload.get("sent_at", 0)
                pipeline_lag_ms = (time.time() - sent_at) * 1000

                print(
                    f"[consumer] offset={msg.offset()}  "
                    f"lag={pipeline_lag_ms:.0f}ms  "
                    f"text={text[:60]}..."
                )

                process_message(text, redis_client, db_conn, http)
                processed += 1

                # Commit only after successful processing.
                # If we crash here, Kafka replays from the last committed offset.
                consumer.commit(message=msg)

        except KeyboardInterrupt:
            print(f"\n[consumer] stopping. {processed} messages processed.")
        finally:
            # close() commits final offsets and leaves the consumer group cleanly.
            # Without this, Kafka waits for session timeout before reassigning partitions.
            consumer.close()
            db_conn.close()
            print("[consumer] done.")


if __name__ == "__main__":
    run()
