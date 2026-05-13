# How DistilBERT processes one sentence — end to end

Let's trace this sentence from AG News through the entire model:

> "NASA launches new telescope to study distant galaxies"

Expected output: `Sci/Tech`

There are 7 steps. Each step has a plain English explanation first,
then the technical detail underneath.

---

## Step 1 — Tokenization: text → token IDs

### Plain English
The model has never seen a "word" in its life. It only understands numbers.
So the first job is to convert words into numbers it can work with.

Think of it like a restaurant menu. Every dish has a number — "Butter Chicken = 42",
"Dal Makhani = 17". The waiter doesn't write "Butter Chicken" on the order slip,
they write "42". The kitchen understands 42. The tokenizer is that conversion system.

DistilBERT has a vocabulary of ~30,000 tokens (not quite words — more like word-pieces).
Every token has a unique ID number. The tokenizer looks each piece up and returns a list of numbers.

Two special tokens are always added:
- `[CLS]` at the start — think of it as a "listening post". By the end of the model,
  it will have absorbed the meaning of the entire sentence. This is what gets classified.
- `[SEP]` at the end — just a full stop marker.

### Technical detail
```
"NASA launches new telescope to study distant galaxies"
         ↓  tokenizer
["[CLS]", "nasa", "launches", "new", "telescope", "to", "study", "distant", "galaxies", "[SEP]"]
         ↓  vocabulary lookup
[  101,   1029,    4888,     2047,    9008,      2000,  2817,    6007,      9721,     102  ]
```
The tokenizer is **fixed** — it never changes during training. Only model weights change.

---

## Step 2 — Embeddings: token IDs → vectors

### Plain English
Numbers like `1029` still don't carry meaning — they're just IDs like a passport number.
The next step converts each ID into a list of 768 numbers that actually *encode meaning*.

Imagine a map where every word has a location. Words with similar meanings live close together.
"Telescope" and "microscope" are neighbours. "Telescope" and "football" are far apart.
That map is 768-dimensional (instead of 2D), but the idea is the same — meaning is geography.

The model looks up each token ID in a big table and gets its 768-number "location".
These numbers were learned during pre-training by reading billions of sentences.

At this stage, each word still doesn't know what's around it. "bank" is just "bank" —
it doesn't know yet if there's a river nearby or a loan officer.

### Technical detail
The embedding table is a matrix of shape `[30,000 × 768]`.
Each token ID is a row index into that matrix.

```
token IDs:   [101,  1029, 4888, 2047, 9008, 2000, 2817, 6007, 9721, 102]
                ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓    ↓
embeddings: [v0,  v1,  v2,  v3,  v4,  v5,  v6,  v7,  v8,  v9]
```
Each `v` is a vector of 768 floats. Result: a `10 × 768` matrix (10 tokens, 768 features each).

---

## Step 3 — Transformer layers: words talk to each other

### Plain English
Here's where the magic happens — and it's actually intuitive once you see the analogy.

Imagine you're reading: *"The NASA scientist studied the galaxy through the telescope."*

Your brain doesn't read word by word in isolation. When you hit "studied", your eyes
go back to "scientist" and "galaxy" to understand *who* studied *what*.
When you hit "telescope", you connect it to "NASA" and "galaxy" to understand the context.

That's exactly what attention does. Every word looks at every other word and asks:
*"How much should you influence my meaning?"*

After this process, "launches" in our sentence knows it's a NASA launch — not a product launch.
"Study" knows it's scientific study — not a school exam.
The `[CLS]` token at the start has been influenced by every word in the sentence
and now carries a rich summary of the whole thing.

DistilBERT does this 6 times in 6 stacked layers. Each pass refines the meaning further.

### Technical detail
Each transformer layer contains two sub-layers:
1. **Multi-head self-attention** — every token attends to every other token
2. **Feed-forward network** — each token's vector is independently transformed

Attention computes three vectors per token:
- **Query (Q)**: "What am I looking for?"
- **Key (K)**: "What do I offer to others?"
- **Value (V)**: "What information do I carry?"

```
attention_score = softmax(Q × Kᵀ / √768)
output = attention_score × V
```

High Q·K similarity = high attention = that token's Value gets mixed in strongly.

```
attention_score("launches", "NASA")    = high  → "NASA" heavily shapes "launches"
attention_score("launches", "distant") = low   → "distant" barely shapes "launches"
```

After 6 layers, each token's 768-vector has been updated 6 times using context
from the whole sentence.

---

## Step 4 — The [CLS] vector: one vector summarises the sentence

### Plain English
After all 6 layers of words influencing each other, we throw away everything
except the `[CLS]` token's final vector.

Think of `[CLS]` like a class representative who sat in a room with every word,
listened to all their conversations, and now has to give a one-line summary
of the whole sentence to the principal.

That summary is a list of 768 numbers. Similar sentences will have similar summaries.
*"NASA builds new rover"* and *"Scientists launch space probe"* will have
summaries that are close together in that 768-dimensional space.

### Technical detail
```
outputs = model(input_ids, attention_mask)
cls_vector = outputs.last_hidden_state[:, 0, :]   # shape: [1, 768]
```
`[:, 0, :]` grabs position 0 — the `[CLS]` token — from the final layer's output.
This is the sentence embedding.

---

## Step 5 — Classification head: summary → prediction

### Plain English
Now we have a 768-number summary of the sentence.
The classification head is a simple decision-maker that reads that summary
and says: *"This sounds most like Sci/Tech."*

It's like handing your class representative's summary to a judge who has
read thousands of previous summaries labelled World / Sports / Business / Sci/Tech
and learned to tell them apart.

The judge outputs 4 scores — one per category. Higher score = more confident.

### Technical detail
The classification head is a single linear layer:
```
logits = cls_vector × W + b
```
Where `W` is a `768 × 4` weight matrix and `b` is a bias of size 4.

```
cls_vector (768 numbers)
      ↓   × W (768 × 4)
logits (4 numbers):  [2.1,  -0.8,  0.3,  3.7]
                    World  Sports  Biz  Sci/Tech
      ↓   softmax
probs:              [0.11,  0.06,  0.08, 0.75]
      ↓   argmax
prediction:         Sci/Tech  ✓
```
This layer is **trained from scratch** — it didn't exist in the pre-trained model.
The transformer layers get gently adjusted; this layer learns everything from our data.

---

## Step 6 — Loss: measuring how wrong we were

### Plain English
The model made a prediction. During training, we know the right answer.
Loss is just a number that says: *"You were this wrong."*

If the model said 75% confidence for the right class → low loss, good job.
If the model said 25% confidence spread equally across all 4 → high loss, very confused.

The entire point of training is to push this number down over many examples.

### Technical detail
We use **cross-entropy loss**, standard for classification:

```
correct label: index 3 (Sci/Tech)
predicted probs: [0.11, 0.06, 0.08, 0.75]

loss = -log(probability of correct class)
     = -log(0.75)
     = 0.29    ← low, model was right and confident
```

Worst case — completely random model:
```
loss = -log(0.25) = 1.39    ← high, model had no idea
```

---

## Step 7 — Backpropagation: learning from the mistake

### Plain English
This is how the model actually learns.

Imagine you throw darts at a board and miss. Someone tells you *"you aimed too far right."*
You adjust slightly left next throw. Over thousands of throws, you get accurate.

Backpropagation is that feedback mechanism. After computing the loss, it traces
backwards through every operation in the model and figures out:
*"Which weights caused this error, and by how much?"*

Then it nudges each weight slightly in the direction that would have reduced the loss.
Learning rate controls how big each nudge is — too large and you overshoot, too small and you never arrive.

After 120,000 sentences × 3 epochs = 360,000 nudges, the weights have shaped themselves
to correctly separate news into 4 categories.

### Technical detail
For every weight `w` in the model:
```
gradient = ∂loss / ∂w          (how much did this weight contribute to the error?)
w = w - learning_rate × gradient
```
We use `learning_rate = 2e-5` (0.00002). Very small, because pre-trained weights are
already good — we want gentle adjustments, not a full reset.

PyTorch handles all of this automatically via `loss.backward()` + `optimizer.step()`.
The HuggingFace `Trainer` calls these for us.

---

## The full picture

```
"NASA launches new telescope to study distant galaxies"
        ↓  tokenizer (fixed — never changes)
[101, 1029, 4888, 2047, 9008, 2000, 2817, 6007, 9721, 102]
        ↓  embedding table (fine-tuned)
10 vectors × 768 numbers — tokens as meaning-coordinates
        ↓  transformer layer 1: words look at each other
        ↓  transformer layer 2: understanding deepens
        ↓  transformer layer 3
        ↓  transformer layer 4
        ↓  transformer layer 5
        ↓  transformer layer 6: [CLS] absorbs full sentence meaning
[CLS] vector — 768 numbers, the sentence's fingerprint
        ↓  classification head (trained from scratch)
logits: [2.1, -0.8, 0.3, 3.7]
        ↓  softmax
probs:  [0.11, 0.06, 0.08, 0.75]
        ↓  argmax
Sci/Tech ✓
```

---

## One-line summaries of each step

| Step | Plain English | Technical name |
|---|---|---|
| 1 | Words → numbers | Tokenization |
| 2 | Numbers → meaning-coordinates | Embedding lookup |
| 3 | Words influence each other | Self-attention (6 layers) |
| 4 | Sentence gets a fingerprint | CLS pooling |
| 5 | Fingerprint → category | Linear classification head |
| 6 | Measure how wrong we were | Cross-entropy loss |
| 7 | Nudge weights to be less wrong | Backpropagation + SGD |
