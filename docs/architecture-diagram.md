# AI Incident Investigator — Architecture & UML Design

## 1. High-Level System Flow

```mermaid
flowchart TD
    A[Customer Complaint / Transaction ID] --> B[Log Ingestion Pipeline]

    subgraph Sources["Log Sources"]
        S1[App Server Logs]
        S2[Payment / Transaction Logs]
        S3[Monitoring Logs]
    end

    S1 --> B
    S2 --> B
    S3 --> B

    B -->|"normalize + tag<br/>(user_id / transaction_id)"| C[(Unified Log Store<br/>Elasticsearch / DataFrame / JSON)]

    C --> D[AI Investigation Engine<br/>LLM + RAG]
    D -->|"reconstructed<br/>timeline"| E[Source Code Correlator<br/>GitPython]

    subgraph Repo["GitHub Repository"]
        R1[(Source Files)]
    end
    R1 --> E

    E -->|"file + line number<br/>+ code snippet"| F[RCA Report Generator]
    D -->|"timeline evidence"| F

    F --> G[["RCA Report<br/>(Root Cause, Evidence,<br/>Short-term Fix, Long-term Fix)"]]

    style A fill:#fef3c7,stroke:#d97706
    style G fill:#dcfce7,stroke:#16a34a
    style C fill:#e0e7ff,stroke:#4338ca
```

## 2. UML Component / Class Diagram

```mermaid
classDiagram
    class SyntheticLogGenerator {
        -scenario: ScenarioType
        -faker: Faker
        +generateAppLogs(n: int) LogEntry[]
        +generatePaymentLogs(n: int) LogEntry[]
        +generateMonitoringLogs(n: int) LogEntry[]
        +injectFailure(scenario: ScenarioType) LogEntry[]
    }

    class LogEntry {
        +timestamp: DateTime
        +source: String
        +level: String
        +user_id: String
        +transaction_id: String
        +message: String
        +raw: Dict
    }

    class LogIngestionPipeline {
        -store: LogStore
        +ingest(rawLogs: LogEntry[]) void
        +normalize(rawLogs: LogEntry[]) LogEntry[]
        +tagCorrelationKeys(logs: LogEntry[]) LogEntry[]
        +query(filter: QueryFilter) LogEntry[]
    }

    class LogStore {
        <<interface>>
        +write(entries: LogEntry[]) void
        +search(query: QueryFilter) LogEntry[]
    }

    class ElasticsearchStore {
        +write(entries: LogEntry[]) void
        +search(query: QueryFilter) LogEntry[]
    }

    class DataFrameStore {
        +write(entries: LogEntry[]) void
        +search(query: QueryFilter) LogEntry[]
    }

    class InvestigationEngine {
        -llm: LLMClient
        -retriever: RAGRetriever
        +investigate(complaint: String, txnId: String) Timeline
        -reconstructTimeline(logs: LogEntry[]) Timeline
        -identifyFailurePoint(timeline: Timeline) FailureEvent
    }

    class RAGRetriever {
        -store: LogStore
        +retrieveRelevantLogs(query: String, txnId: String) LogEntry[]
    }

    class Timeline {
        +events: TimelineEvent[]
        +failurePoint: FailureEvent
    }

    class TimelineEvent {
        +timestamp: DateTime
        +description: String
        +logRef: LogEntry
    }

    class FailureEvent {
        +description: String
        +suspectedModule: String
        +confidence: Float
    }

    class SourceCodeCorrelator {
        -repo: GitRepo
        +locateFailure(failure: FailureEvent) CodeReference
        -searchRepo(keyword: String) CodeReference[]
    }

    class GitRepo {
        -path: String
        +clone(url: String) void
        +getFile(path: String) String
        +blame(path: String, line: int) CommitInfo
    }

    class CodeReference {
        +filePath: String
        +lineNumber: int
        +snippet: String
        +commitInfo: CommitInfo
    }

    class ReportGenerator {
        +generateRCA(timeline: Timeline, code: CodeReference) RCAReport
    }

    class RCAReport {
        +rootCause: String
        +evidence: Evidence[]
        +shortTermFix: String
        +longTermFix: String
        +toMarkdown() String
    }

    class Evidence {
        +logLines: LogEntry[]
        +codeSnippet: CodeReference
    }

    SyntheticLogGenerator --> LogEntry : creates
    LogIngestionPipeline --> LogStore : uses
    LogStore <|.. ElasticsearchStore
    LogStore <|.. DataFrameStore
    InvestigationEngine --> RAGRetriever : uses
    RAGRetriever --> LogStore : queries
    InvestigationEngine --> Timeline : produces
    Timeline --> TimelineEvent
    Timeline --> FailureEvent
    SourceCodeCorrelator --> GitRepo : uses
    SourceCodeCorrelator --> CodeReference : produces
    ReportGenerator --> RCAReport : produces
    RCAReport --> Evidence
    ReportGenerator ..> InvestigationEngine : consumes Timeline
    ReportGenerator ..> SourceCodeCorrelator : consumes CodeReference
```

## 3. UML Sequence Diagram — Scenario C Walkthrough (Silent Payment Failure)

```mermaid
sequenceDiagram
    actor User as Customer
    participant API as Support/Trigger API
    participant Ingest as Log Ingestion Pipeline
    participant Store as Unified Log Store
    participant Engine as Investigation Engine (LLM+RAG)
    participant Repo as Source Code Correlator
    participant Git as GitHub Repo
    participant Report as Report Generator

    User->>API: "I paid $50, subscription still expired"
    API->>Ingest: pull logs (user_id / txn_id)
    Ingest->>Store: normalize + write tagged entries
    API->>Engine: investigate(complaint, txn_id)
    Engine->>Store: retrieve relevant logs (RAG)
    Store-->>Engine: app, payment, monitoring log entries

    Engine->>Engine: reconstruct timeline
    Note over Engine: 10:03:01 Renew clicked<br/>10:03:02 Payment gateway → success<br/>10:03:03 Update subscription → exception<br/>10:03:03 Exception swallowed, API returns 200

    Engine->>Repo: locateFailure(failure_signature)
    Repo->>Git: search repo for matching handler
    Git-->>Repo: SubscriptionService.java:142
    Repo-->>Engine: CodeReference (file, line, snippet)

    Engine->>Report: generateRCA(timeline, codeRef)
    Report->>Report: assemble root cause + evidence + fixes
    Report-->>API: RCAReport (markdown)
    API-->>User: "Root cause found: exception swallowed at line 142..."
```

## Notes on the design

- **Loose coupling via `LogStore` interface** lets you swap Elasticsearch for a simple DataFrame/JSON store during early development (per building block #2) without touching the ingestion or investigation logic.
- **`InvestigationEngine` and `SourceCodeCorrelator` are independent stages** — the engine only needs a `FailureEvent` signature (e.g. exception type, module name, timestamp) to hand off to the correlator, so scenarios A/B/C all flow through the same pipeline shape.
- **`RCAReport.toMarkdown()`** is the natural seam for turning the structured object into the final human-readable report (and could equally emit JSON/HTML for a dashboard).
- Scenarios A and B reuse every box in the flow diagram unchanged — only the `SyntheticLogGenerator.injectFailure()` logic and the shape of `FailureEvent` differ per scenario.
