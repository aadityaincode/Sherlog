# AI-Driven Log Analysis & Root Cause Investigation

HCLTech Hackathon — AMS / Observability, Use Case #02.

An AI-powered investigation assistant that moves from a customer complaint to an
evidence-backed root cause in minutes: it ingests multi-source logs, reconstructs
the transaction journey with an LLM, correlates the failure to the exact line of
source code, and generates a structured RCA report.

## How it works

```
[1] Synthetic log generator ──> app.log / transactions.json / monitoring.log
[2] Ingestion pipeline      ──> normalized, queryable log store
[3] AI investigation engine ──> failure point + cited log evidence (LLM + RAG)
[4] Code correlator + RCA   ──> root cause @ file:line, fixes, journey diagram
```

Primary scenario: **C — Silently Failed Financial Transaction** (payment succeeds,
downstream update silently fails, customer sees no error).

## Data

- `data/samples/` — small committed examples of each log format, for browsing on GitHub. Not used by the pipeline.
- `data/generated/` — the real dataset (gitignored, regenerate with the command below).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY
docker compose -f src/investigation/docker.yml up -d   # elasticsearch + kibana
```

## Run

```bash
python -m src.log_generator   # writes data/generated/

cd src/investigation
python ingest.py ../../data/generated/app.log ../../data/generated/monitoring.log ../../data/generated/transactions.json
python search.py              # smoke test: prints a reconstructed transaction timeline
```