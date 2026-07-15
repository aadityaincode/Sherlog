# AI Incident Investigator — Architecture & UML Design

## 1. High-Level System Flow

```mermaid
flowchart TD
    A[Customer Complaint / Transaction ID] --> D

    subgraph Sources["Log Sources (synthetic, Scenario C)"]
        S1[app.log]
        S2[transactions.json]
        S3[monitoring.log]
    end

    S1 --> B[Log Ingestion Pipeline<br/>parsers.py / ingest.py]
    S2 --> B
    S3 --> B

    B -->|"normalize + tag<br/>(user_id / txn_id)"| C[(Unified Log Store<br/>Elasticsearch)]

    C -->|"query tools:<br/>timeline / user / text / time-window"| D[AI Investigation Engine<br/>Gemini function-calling agent]
    D -->|"failure_point +<br/>cited evidence"| E[Source Code Correlator<br/>GitPython]

    subgraph Repo["novastream-billing (GitHub)"]
        R1[(Source Files)]
    end
    R1 --> E

    E -->|"file:line + snippet"| F[RCA Report Generator<br/>LLM writeup]
    D -->|"evidence passthrough"| F
    D -->|"reconstructed timeline"| J[Journey Diagram<br/>Mermaid, deterministic]

    F --> G[["RCA Report<br/>(Root Cause, Evidence,<br/>Short-term Fix, Long-term Fix)"]]
    J --> G

    style A fill:#fef3c7,stroke:#d97706,color:#1f2937
    style G fill:#dcfce7,stroke:#16a34a,color:#1f2937
    style C fill:#e0e7ff,stroke:#4338ca,color:#1f2937
```

## 2. UML Component / Class Diagram

```mermaid
classDiagram
    class SyntheticLogGenerator {
        -vocab: VocabularyDoc
        -faker: Faker
        +generate_normal_transaction() Transaction
        +generate_broken_transaction() Transaction
        +generate_declined_transaction() Transaction
        +write_dataset(out_dir) files
    }

    class LogEntry {
        +timestamp: DateTime
        +source: String
        +level: String
        +component: String
        +user_id: String
        +txn_id: String
        +message: String
        +raw: Dict
    }

    class IngestionPipeline {
        +parse_app_log(path) LogEntry[]
        +parse_monitoring_log(path) LogEntry[]
        +parse_transactions(path) LogEntry[]
        +run(paths) int
    }

    class ElasticsearchStore {
        +create_index(mapping) void
        +bulk_write(entries: LogEntry[]) void
    }

    class QueryTools {
        +get_timeline_by_txn(txn_id) LogEntry[]
        +search_by_user(user_id) LogEntry[]
        +full_text_search(query) LogEntry[]
        +errors_near(timestamp) LogEntry[]
    }

    class InvestigationEngine {
        -llm: GeminiClient
        -tools: QueryTools
        +investigate(complaint) InvestigationResult
        -agent_loop() max 6 rounds
        -submit_investigation_result() forced structured output
    }

    class InvestigationResult {
        +issue_found: bool
        +failure_point: String
        +service: String
        +error_message: String
        +evidence: String[]
    }

    class SourceCodeCorrelator {
        -repo: GitRepo (GitPython clone)
        +correlate(failure_point) CodeReference
    }

    class CodeReference {
        +file: String
        +line: int
        +code_snippet: String
    }

    class RCAGenerator {
        +generate_rca(investigation, code) RCAReport
        +render_markdown(report) String
    }

    class JourneyDiagram {
        +timeline_to_mermaid(timeline, issue_found) String
    }

    class RCAReport {
        +root_cause_statement: String
        +customer_impact: String
        +short_term_fix: String
        +long_term_fix: String
        +evidence: Evidence
    }

    SyntheticLogGenerator --> LogEntry : creates
    IngestionPipeline --> LogEntry : normalizes into
    IngestionPipeline --> ElasticsearchStore : bulk loads
    QueryTools --> ElasticsearchStore : queries
    InvestigationEngine --> QueryTools : calls as LLM tools
    InvestigationEngine --> InvestigationResult : produces
    SourceCodeCorrelator --> CodeReference : produces
    RCAGenerator --> RCAReport : produces
    RCAGenerator ..> InvestigationResult : consumes
    RCAGenerator ..> CodeReference : consumes
    JourneyDiagram ..> QueryTools : renders timeline from
```

## 3. UML Sequence Diagram — Scenario C Walkthrough (Silent Payment Failure)

```mermaid
sequenceDiagram
    actor User as Customer
    participant Pipe as Pipeline (CLI / Streamlit)
    participant Engine as Investigation Engine (Gemini agent)
    participant Store as Elasticsearch
    participant Corr as Source Code Correlator
    participant Git as novastream-billing repo
    participant Report as RCA Generator

    User->>Pipe: "I was charged, subscription still expired, no email"
    Pipe->>Engine: investigate(complaint)
    Engine->>Store: search_by_user(USR-xxxxx)
    Store-->>Engine: candidate transactions
    Engine->>Store: get_timeline_by_txn(TXN-xxxx)
    Store-->>Engine: timeline incl. time-window-correlated errors

    Note over Engine: payment APPROVED, then novastream.db ERROR,<br/>messages 8 (ACTIVE) and 10 (email) ABSENT,<br/>API still returned 200 → broken-renewal signature
    Engine->>Engine: submit_investigation_result (forced structured output)
    Engine-->>Pipe: {issue_found, failure_point, service, error, evidence}

    Pipe->>Corr: correlate("SubscriptionService.renew")
    Corr->>Git: clone/pull, locate class + method + except handler
    Git-->>Corr: app/services/subscription_service.py:38
    Corr-->>Pipe: CodeReference (file, line, snippet)

    Pipe->>Report: generate_rca(investigation, codeRef)
    Report-->>Pipe: RCA report (root cause, impact, fixes)
    Pipe->>Pipe: timeline_to_mermaid (journey diagram, incl. NEVER-HAPPENED steps)
    Pipe-->>User: reports/rca_TXN-xxxx.md
```

## Notes on the design

- **The agent decides its own query strategy.** The engine isn't a fixed retrieval pipeline: Gemini chooses which of the four query tools to call, reads the results, and follows the evidence (complaint → user → transaction → timeline). A forced `submit_investigation_result` tool call ends the loop, guaranteeing schema-valid output — including `issue_found: false` for clean transactions, so it doesn't hallucinate failures.
- **Evidence is never LLM-generated.** Log lines pass through verbatim from the store; the code location comes from deterministic GitPython search; the journey diagram is deterministic templating. The LLM writes analysis and fixes, not citations — and the scoring harness (`score.py`) verifies every cited line against the parsed log corpus.
- **`InvestigationEngine` and `SourceCodeCorrelator` are independent stages** — the correlator only needs a `failure_point` string, so scenarios A/B would flow through the same pipeline shape with a different planted bug.
- **Absence as evidence:** the broken-renewal signature is defined by log lines that are *missing* (subscription-ACTIVE, confirmation email). The journey diagram draws those as dashed NEVER-HAPPENED arrows — that's the story of Scenario C in one picture.
