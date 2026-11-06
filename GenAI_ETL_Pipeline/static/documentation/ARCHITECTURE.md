# Architecture — FlowForge 1.5.0

Updated 6 November 2026. Start with [SYSTEM_GUIDE.md](SYSTEM_GUIDE.md) for the consolidated implementation and operating guide.

## Implemented architecture

![Implemented local architecture](diagrams/architecture-current.svg)

One FastAPI process hosts the review API, deterministic ETL functions and optional SMTP worker. SQLite stores raw/protected source snapshots, Gold rollups, SCD2 history, source profiles/contracts, quality results, warehouse rows, immutable pipeline versions, proposals, audit events, reviewed reference chunks and the notification outbox. Optional model calls produce proposals, not executable authority.

```mermaid
flowchart TB
  UI[Browser console / CLI] --> API[FastAPI / validated data API boundary]
  subgraph APP[Implemented: one local application process]
    API --> ING[Ingest structured files and HTTPS snapshots]
    ING --> ST[Chunked CSV / TSV / Parquet staging]
    API --> AI[Recipe and multi-source schema discovery]
    AI --> MV[Field equivalences and mapping validation]
    ST --> M[Disk-backed merge and join preview]
    ING --> M
    MV --> K[Full-snapshot key checks and join preview]
    M --> K
    K --> MR[Interactive data model and human mapping review]
    MR -->|reviewed join| M
    MR -->|approved bindings| EXEC
    API --> CH[Conversation / diff / human review / versions]
    API --> EXEC[Deterministic ETL and data quality]
    ST --> EXEC
    M --> EXEC
    REF[Reviewed references / SQLite FTS5] -. optional context .-> AI
    GUARD[Typed operations / source and policy checks] --> CH
    CH --> PRE[Pure preview: zero publication]
    PRE --> HUMAN[Human approves exact candidate]
    HUMAN --> ACTIVE[Activate immutable version]
    ACTIVE --> AUTH[Separate execution authorization]
    AUTH --> EXEC
    EXEC --> STREAM[Bounded row batches for large runs]
    STREAM --> GATE{DQ and target contract pass?}
    GATE -->|yes| GOLD[Atomic warehouse upsert]
    GATE -->|no| HOLD[No new-batch publication]
    STREAM --> SILVER[Silver and quarantine]
    DB[(SQLite WAL: sources / control / audit / knowledge)]
    ING --> DB
    CH --> DB
    GOLD --> DB
    SILVER --> DB
    REF --> DB
    EVAL[Local regression checks] --> DB
    CH --> OUTBOX[Transactional notification outbox]
    OUTBOX --> MAIL[Optional in-process SMTP worker]
  end
  AI -. schema-constrained proposals .-> LLM[Optional configured LLM routes]
  MAIL -. TLS / bounded delivery retries .-> SMTP[Configured SMTP provider]
  RB[Approved configuration rollback] --> ACTIVE
  NOTE[Rollback creates a new config version; existing warehouse data is not reverted]
  RB -.-> NOTE
```

## Multi-source mapping and join review

![AI mapping, key checks and review](diagrams/architecture-mapping.svg)

The model suggests equivalent columns independently of the integration strategy. Discovery checks the proposed target key across the applicable snapshots. A reviewed join stages a combined source with lineage, then a person reviews the final mappings. Merge and join preview use a disk-backed index. The interactive Data model traces target columns to their mapped sources.

See [Multi-source mapping](MULTI_SOURCE_MAPPING.md) and the [editable Mermaid workflow](diagrams/architecture-mapping.mmd).

## Proposed cloud-native target

![Proposed cloud target](diagrams/architecture-cloud-target.svg)

```mermaid
flowchart TB
  subgraph FUTURE[Proposed only: not deployed]
    UI[Review console / named users] --> ID[TLS ingress / OIDC / RBAC]
    ID --> API[Stateless API replicas]
    API --> PG[(Managed PostgreSQL control plane)]
    API --> Q[Durable queue]
    Q --> W[Independent ETL / model / notification workers]
    W --> PG
    W --> OBJ[(Encrypted versioned object storage)]
    W --> WH[(Chosen cloud warehouse)]
    W --> E[Durable key checks, join previews and lineage evidence]
    E --> PG
    W --> EXT[Approved LLM / SMTP endpoints]
    SEC[Managed secrets / workload identity] --> W
    API --> OBS[Metrics / traces / alerts]
    W --> OBS
    RELEASE[CI gates / staging / immutable images / tested rollback]
  end
  subgraph LATER[Optional only after measured need]
    VEC[pgvector: labelled semantic-retrieval benefit]
    CACHE[Redis: repeated expensive requests]
  end
  W -. future candidate .-> VEC
  API -. future candidate .-> CACHE
```

The cloud target requires identity/authorization, managed control/object storage, queue-backed workers, production integration tests, observability and controlled releases. Vector search and Redis remain optional candidates, not baseline dependencies. See [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md).

## Design boundaries

- Preview never publishes. Activation, execution and rollback are distinct reviewed actions.
- Configuration rollback does not reverse warehouse data or cancel in-flight work.
- Shared-token review is not named-user SSO or separation of duties.
- SMTP delivery is at-least-once, advisory and disabled until configured.
- Retrieved knowledge supplies optional reference context; current search is SQLite FTS5/BM25, not vector search.
- The existing Docker packaging is not a deployed cloud-native platform.

The [browser architecture view](diagrams/architecture.html), SVGs and `.mmd` files can be shared or edited without external scripts or internet access.
