# Project Knowledge Base

This folder documents the concepts, decisions, and trade-offs behind every
component of this project. Written while building — so the reasoning is fresh.

## Architecture

- [Architecture — technical HLD + LLD](architecture.md)
  System context, component view, sequence diagrams for every flow, schemas,
  failure modes, deployment topology. All diagrams in Mermaid.
- [Architecture, in plain English](architecture-explained.md)
  Same architecture explained as a newsroom analogy. Walks one headline through
  the system end-to-end.

## Architecture Decisions

- [Decisions log (ADRs)](decisions.md)
  The **why** behind every major architectural choice — model selection, broker
  choice, delivery semantics, storage, hosting, observability pattern. Twelve
  decisions, each with context and consequences.

## Concepts (per-component deep dives)

- [Fine-tuning vs Training from Scratch](concepts/fine-tuning.md)
- [How DistilBERT processes one sentence — end to end](concepts/transformer-end-to-end.md)
- [The Serving Layer — FastAPI + Prometheus](concepts/serving-layer.md)
- [The Kafka Pipeline — Producer + Consumer](concepts/kafka-pipeline.md)
- [Drift Monitoring — Evidently](concepts/drift-monitoring.md)
- [Grafana Monitoring — Metrics Dashboard](concepts/grafana-monitoring.md)

## Build Log

- [Problems, Solutions, and Lessons](build-log.md)
  Fifteen real problems hit during development with root cause, fix, and lesson
  for each. Written as it happened, not reconstructed afterwards.

---

**How these docs relate to each other:**

| If you want to know... | Read... |
|---|---|
| What components exist and how they connect | `architecture.md` |
| How a single request flows through the system | `architecture.md` §4 (sequence diagrams) |
| The same thing, but as a story | `architecture-explained.md` |
| **Why** a particular component was chosen | `decisions.md` |
| **How** a specific component works internally | `concepts/<component>.md` |
| What broke during development and how it was fixed | `build-log.md` |
