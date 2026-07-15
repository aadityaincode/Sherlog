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
cp .env.example .env   # fill in GEMINI_API_KEY
docker compose -f src/ingestion/docker.yml up -d   # elasticsearch + kibana
```

## Run

```bash
# 1. generate the dataset and load it into Elasticsearch
python -m src.log_generator
cd src/ingestion
python ingest.py ../../data/generated/app.log ../../data/generated/monitoring.log ../../data/generated/transactions.json
cd ../..

# 2. the whole pipeline, one command: complaint in -> RCA report + diagram out
#    (the user id below is from the default dataset; if you regenerated with a
#    different --seed, grab one from data/generated/answer_key.json instead)
python src/pipeline.py "Customer USR-95005 says they were charged but their subscription still shows expired and no email arrived"

# or the UI: paste a complaint, watch the investigation happen
streamlit run ui/app.py
```

Reports land in `reports/` as markdown (with an embedded Mermaid journey
diagram) plus the raw JSON. Two known-good runs are committed there as
demo fallbacks.

## Verify

```bash
cd src/investigation
python score.py   # full accuracy suite vs the hidden answer key: 5 broken + 2 clean + 2 free-text complaints
```

## Layout

- `src/log_generator/` — synthetic dataset for Scenario C (normal + broken + declined renewals)
- `src/ingestion/` — parsers for the 3 log formats -> normalized schema -> Elasticsearch
- `src/investigation/` — the brain: query tools + Gemini agent loop + scoring harness
- `src/correlation/` — GitPython: failure point -> `file:line` + snippet in the demo-app repo
- `src/reporting/` — RCA report writer + Mermaid journey diagram
- `src/pipeline.py` — end-to-end entrypoint
- `ui/app.py` — thin Streamlit front end
- `docs/` — architecture diagram + shared vocabulary doc

The demo "client codebase" is a separate public repo:
[novastream-billing](https://github.com/aadityaincode/novastream-billing).