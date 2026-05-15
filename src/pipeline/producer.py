import json
import os
import random
import time

from confluent_kafka import Producer
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")


def make_kafka_config() -> dict:
    return {
        "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": os.environ["KAFKA_USERNAME"],
        "sasl.password": os.environ["KAFKA_PASSWORD"],
    }


def on_delivery(err, msg):
    # Kafka delivery is async — the producer sends and moves on.
    # This callback fires when the broker confirms receipt (or fails).
    # In production you'd alert on errors; here we just print.
    if err:
        print(f"[producer] delivery failed: {err}")
    else:
        print(f"[producer] delivered → offset {msg.offset()}")


def load_articles() -> list[str]:
    from datasets import load_dataset  # lazy — only needed at runtime, not on import
    print("[producer] loading AG News test articles...")
    dataset = load_dataset("ag_news", split="test")
    articles = [row["text"] for row in dataset]
    print(f"[producer] {len(articles)} articles loaded")
    return articles


def run(rate_per_sec: float = 1.0):
    articles = load_articles()
    producer = Producer(make_kafka_config())
    topic = os.environ["KAFKA_TOPIC"]
    interval = 1.0 / rate_per_sec

    print(f"[producer] streaming to topic '{topic}' at {rate_per_sec} msg/sec")
    print("[producer] press Ctrl+C to stop\n")

    sent = 0
    try:
        while True:
            text = random.choice(articles)
            payload = json.dumps({
                "text": text,
                "sent_at": time.time(),
            })

            producer.produce(
                topic,
                value=payload.encode("utf-8"),
                callback=on_delivery,
            )
            # poll() lets the producer handle delivery callbacks.
            # Without it, callbacks never fire and the internal queue fills up.
            producer.poll(0)

            sent += 1
            print(f"[producer] sent #{sent}: {text[:60]}...")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n[producer] stopping. flushing {producer.__len__()} pending messages...")
    finally:
        # flush() blocks until all in-flight messages are delivered or fail.
        # Never skip this — messages in the internal buffer are lost if you don't.
        producer.flush()
        print("[producer] done.")


if __name__ == "__main__":
    run(rate_per_sec=1.0)
