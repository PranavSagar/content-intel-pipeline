# Fine-tuning vs Training from Scratch

## The core idea

A transformer model like DistilBERT is not built for your task.
It is built to understand language — full stop.

Training it to understand language took Hugging Face weeks on hundreds of GPUs,
processing billions of sentences from the internet. The model learned things like:
- "bank" means different things near "river" vs "money"
- "not good" is negative even though "good" is positive
- Questions have a different structure than statements

All of that knowledge lives in the model's **weights** — ~66 million numbers
that encode everything it learned about language.

**Fine-tuning means: take those weights as a starting point, then teach the
model your specific task on your specific data.**

You are not starting from zero. You are giving the model a new job,
not a new brain.

---

## Analogy that maps to your background

Think of it like deploying a new microservice.

You don't write a TCP stack from scratch — you use the OS's networking layer,
which was built and battle-tested by others. You build your business logic on top.

DistilBERT is the TCP stack. Your 4-class news classifier is the business logic.
Fine-tuning is the integration layer between them.

---

## What physically happens during fine-tuning

DistilBERT's architecture has two parts:

```
Input text
    ↓
[ Transformer layers ]   ← "the brain" — understands language
    ↓
[ Classification head ]  ← "the output" — maps to your classes
```

When we fine-tune for AG News classification (4 classes: World, Sports, Business, Sci/Tech):

1. We **keep** the transformer layers (pre-trained weights loaded from HuggingFace)
2. We **add** a new classification head — a small linear layer that maps
   the transformer's output to 4 numbers (one per class)
3. We train on AG News — the transformer layers update slightly to be better
   at *news language*, and the classification head learns to separate the 4 categories

The transformer layers don't change drastically — they just get nudged.
The classification head learns from scratch, but it's tiny so it's fast.

---

## Why not train from scratch?

| | Train from scratch | Fine-tune |
|---|---|---|
| Data needed | Billions of sentences | Thousands of examples |
| Compute | Weeks on 100s of GPUs | Minutes/hours on 1 GPU |
| Cost | ~$1M+ | ~$0 on free tier |
| Final accuracy | Depends on your data | Usually better (starts from strong base) |

Training from scratch only makes sense if your domain is so unusual that
general language models have no useful knowledge — e.g., genomic sequences,
proprietary financial codes, binary protocols. For news text in English, DistilBERT
already knows almost everything it needs.

---

## Why DistilBERT and not BERT or GPT?

**BERT** is the original model from Google (2018). DistilBERT is a distilled
(compressed) version — 40% smaller, 60% faster, retains 97% of BERT's accuracy.
For a classification task (not generation), it is the right size/performance trade-off.

**GPT-style models** (like GPT-2, LLaMA) are generative — they predict the next word.
You *can* use them for classification but it's like using a sledgehammer to tap a nail.
They are much larger, slower, and more expensive for a task that doesn't need generation.

For a production system serving 800K+ QPS (like you've built), smaller + faster + accurate
enough beats largest and most capable every time.

---

## In this project

- **Base model**: `distilbert-base-uncased` (66M parameters, English, lowercase input)
- **Dataset**: AG News — 120K training articles, 4 classes
- **What we add**: A linear classification head (4 outputs)
- **What we track in MLflow**: accuracy, f1-score, learning rate, batch size, epochs
- **Expected accuracy**: ~92-93% (literature benchmark on AG News)
- **Training time on M-series Mac (MPS)**: ~20-30 minutes for 3 epochs

---

## Trade-offs we accepted

**Why 3 epochs and not more?**
AG News is a clean, well-defined dataset. After 3 epochs, the model has seen
every training example 3 times and typically converges. More epochs = overfitting risk
without meaningful accuracy gain. We use early stopping to catch this automatically.

**Why `uncased`?**
Uncased means the tokenizer lowercases everything before processing.
"World" and "world" are treated identically. For news classification this is fine —
case doesn't change the topic. Cased models are needed when case carries meaning
(e.g., Named Entity Recognition: "Apple" the company vs "apple" the fruit).

**What we gave up vs a larger model:**
A model like `bert-large` or `roberta-large` might squeeze out 1-2% more accuracy.
We chose DistilBERT because this is a serving-focused project — the bottleneck
is inference latency and throughput, not squeezing the last 1% of accuracy.
