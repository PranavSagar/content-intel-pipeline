# How DistilBERT processes one sentence — end to end

Let's trace this sentence from AG News through the entire model:

> "NASA launches new telescope to study distant galaxies"

Expected output: `Sci/Tech`

---

## Step 1 — Tokenization: text → token IDs

The model cannot read words. It only understands numbers.

The tokenizer breaks the sentence into **tokens** — small chunks it has a vocabulary for.
DistilBERT's vocabulary has ~30,000 entries.

```
"NASA launches new telescope to study distant galaxies"
         ↓  tokenizer
["[CLS]", "nasa", "launches", "new", "telescope", "to", "study", "distant", "galaxies", "[SEP]"]
         ↓  vocabulary lookup (each token → integer)
[  101,   1029,    4888,     2047,    9008,      2000,  2817,    6007,      9721,     102  ]
```

Two special tokens are always added:
- `[CLS]` (token 101) — "classification token". Placed at the start. By the end of the
  model, this single token's vector will represent the meaning of the entire sentence.
  This is what the classification head reads.
- `[SEP]` (token 102) — "separator". Marks the end of the sentence.

**Key insight**: The tokenizer is fixed. It never changes during training.
Only the model weights change.

---

## Step 2 — Embeddings: token IDs → vectors

Each token ID gets looked up in an **embedding table** — a matrix of shape
`[vocab_size × embedding_dim]` = `[30,000 × 768]`.

So token `1029` (nasa) → a vector of 768 numbers.
Token `4888` (launches) → a different vector of 768 numbers.

```
token IDs:  [101,  1029, 4888, 2047, 9008, 2000, 2817, 6007, 9721, 102]
               ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓
embeddings: [v0,  v1,  v2,  v3,  v4,  v5,  v6,  v7,  v8,  v9]   ← each is 768 numbers
```

At this point: no token knows anything about the other tokens.
"NASA" doesn't know it appears next to "telescope". They are just independent vectors.

**What are these 768 numbers?**
They encode semantic meaning learned during pre-training.
Similar words have similar vectors. "telescope" and "microscope" are closer together
than "telescope" and "football".

Think of it like a 768-dimensional coordinate system where meaning is geography.

---

## Step 3 — Transformer layers: vectors talk to each other

This is the core of the model. DistilBERT has 6 transformer layers stacked on top of
each other. Each layer does the same thing: **let every token look at every other token
and update its own representation based on what it sees.**

This mechanism is called **attention**.

### What attention actually does

Before attention, "bank" in these two sentences has the same vector:
- "The river bank was flooded"
- "The bank approved the loan"

After attention, "bank" in sentence 1 has been influenced by "river" and "flooded",
and "bank" in sentence 2 has been influenced by "approved" and "loan".
They now have *different* vectors even though they started identical.

In our sentence:
- "launches" gets influenced by "NASA" → understands this is a space launch, not a product launch
- "study" gets influenced by "galaxies" → understands scientific study, not school study
- `[CLS]` gets influenced by *every* token → by the end it's a compressed summary of the sentence

### Mathematically (simplified)

For each token, attention computes three things:
- **Query (Q)**: "What am I looking for?"
- **Key (K)**: "What do I offer to others?"
- **Value (V)**: "What information do I carry?"

Each token asks its Query against every other token's Key.
High similarity = high attention score = that token's Value gets mixed in more.

```
attention_score("launches", "NASA") = high   → "NASA" heavily influences "launches"
attention_score("launches", "the")  = low    → "the" barely influences "launches"
```

After 6 layers of this, every token's vector has been updated 6 times, incorporating
context from the whole sentence.

---

## Step 4 — The [CLS] vector: sentence representation

After 6 transformer layers, we take only the `[CLS]` token's final vector.
It's a 768-dimensional vector that now encodes the meaning of the entire sentence,
shaped by 6 rounds of every token influencing every other token.

```
After 6 transformer layers:
[CLS] vector = [0.23, -1.4, 0.87, ..., 0.11]   ← 768 numbers
```

This is the sentence's "fingerprint". Similar sentences have similar fingerprints.

---

## Step 5 — Classification head: vector → prediction

The classification head is just a linear layer (a matrix multiply):

```
[CLS] vector (768 numbers)
         ↓   × weight matrix (768 × 4)
logits (4 numbers):  [2.1,  -0.8,  0.3,  3.7]
                    World  Sports  Biz  Sci/Tech
         ↓   softmax (convert to probabilities)
probs:              [0.11,  0.06,  0.08, 0.75]
         ↓   argmax
prediction:         Sci/Tech  ✓
```

The 4 output numbers are called **logits** — raw unnormalized scores.
Softmax converts them to probabilities that sum to 1.
Argmax picks the highest probability class.

---

## Step 6 — Loss: how wrong were we?

During training, we know the correct label (Sci/Tech = index 3).
We compute **cross-entropy loss** — a single number measuring how wrong the model was.

```
correct label: index 3 (Sci/Tech)
predicted probs: [0.11, 0.06, 0.08, 0.75]
loss = -log(0.75) = 0.29    ← low loss, model was right and confident
```

If the model had predicted [0.25, 0.25, 0.25, 0.25] (totally confused):
```
loss = -log(0.25) = 1.39    ← high loss, model had no idea
```

---

## Step 7 — Backpropagation: how weights update

This is where learning happens. The loss is a signal: "you were this wrong."

Backpropagation traces that signal backwards through every operation in the model,
computing how much each weight contributed to the error.

**Gradient**: the direction and size each weight should change to reduce the loss.

```
for every weight in the model:
    weight = weight - (learning_rate × gradient)
```

Learning rate (e.g., 2e-5 = 0.00002) controls how big each step is.
Too large → weights overshoot and bounce around.
Too small → training takes forever.

After one sentence: weights change by a tiny amount.
After 120,000 sentences × 3 epochs: weights have been nudged 360,000 times
into a configuration that correctly classifies news.

---

## The full picture

```
"NASA launches new telescope to study distant galaxies"
         ↓  tokenizer (fixed, never changes)
token IDs: [101, 1029, 4888, ...]
         ↓  embedding lookup (these weights get fine-tuned)
token vectors: 10 × 768 matrix
         ↓  transformer layer 1 (attention + feedforward, weights fine-tuned)
updated vectors: tokens now aware of neighbors
         ↓  transformer layer 2
         ↓  transformer layer 3
         ↓  transformer layer 4
         ↓  transformer layer 5
         ↓  transformer layer 6
[CLS] vector: 768 numbers encoding full sentence meaning
         ↓  classification head (trained from scratch)
logits: [2.1, -0.8, 0.3, 3.7]
         ↓  softmax
probs:  [0.11, 0.06, 0.08, 0.75]
         ↓  argmax
output: Sci/Tech ✓
```

---

## What fine-tuning changes vs pre-training

During **pre-training** (done by HuggingFace, not us):
- The embedding table was built
- All 6 transformer layers were trained on billions of sentences
- The model learned language

During **fine-tuning** (what we do):
- The transformer layers get *slightly* adjusted for news language
- The classification head is trained from zero — it didn't exist before
- The embedding table gets slightly adjusted
- The tokenizer is untouched

The pre-trained weights are the reason we can do this in 30 minutes instead of weeks.
We are standing on the shoulders of HuggingFace's compute bill.
