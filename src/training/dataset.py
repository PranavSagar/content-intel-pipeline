from datasets import load_dataset
from transformers import DistilBertTokenizerFast

# AG News has 4 categories. These mappings are the single source of truth
# used by both training (loss computation) and serving (label decoding).
LABELS = ["World", "Sports", "Business", "Sci/Tech"]
ID2LABEL = {i: label for i, label in enumerate(LABELS)}
LABEL2ID = {label: i for i, label in enumerate(LABELS)}
NUM_LABELS = len(LABELS)


def load_ag_news(
    tokenizer_name: str = "distilbert-base-uncased",
    max_length: int = 128,
):
    dataset = load_dataset("ag_news")
    tokenizer = DistilBertTokenizerFast.from_pretrained(tokenizer_name)

    def tokenize(batch):
        return tokenizer(
            batch["text"],
            truncation=True,
            max_length=max_length,
            padding="max_length",
        )

    # batched=True processes 1000 articles at once instead of one by one — ~20x faster
    dataset = dataset.map(tokenize, batched=True, batch_size=1000)

    # Trainer expects "labels" (plural) — AG News ships it as "label" (singular)
    dataset = dataset.rename_column("label", "labels")

    # Return PyTorch tensors, not Python lists
    dataset.set_format("torch", columns=["input_ids", "attention_mask", "labels"])

    return dataset, tokenizer
