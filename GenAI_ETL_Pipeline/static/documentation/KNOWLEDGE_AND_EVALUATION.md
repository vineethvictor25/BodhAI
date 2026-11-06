# Optional grounded knowledge and regression checks

This increment is deliberately small: reviewed policy/runbook retrieval, not a new agent platform. It adds no runtime dependency. Retrieval uses SQLite FTS5/BM25, not a vector database or trained reranker. No external model request is made for indexing, keyword search, cited excerpts or the bundled regression evaluation.

## Console workflow

1. Open **Knowledge & checks** after restarting the server and refreshing the page.
2. Register a title and reference text. Explicitly select the review checkbox to allow retrieval. Unreviewed drafts remain excluded. The shared local token is not a production reviewer identity.
3. Use **Retrieve cited excerpts** to inspect the exact source chunk and character offsets.
4. In Pipeline Studio, optionally select **Include reviewed reference excerpts** when generating a semantic mapping or inferring a multi-source schema. The model receives up to four matching reference chunks in addition to the existing bounded source profiles.
5. Review the mapping as before. Retrieved rules cannot invent source paths, override approval, change the validated execution vocabulary or bypass quality checks. Returned knowledge citation IDs must belong to the retrieved set.

Optional retrieval is **off by default**. It is appropriate when organization-specific rules matter; it is unnecessary for obvious source-to-target name matches. We have not demonstrated a live mapping accuracy improvement over the existing sample-aware mapper. No additional live calls were made in this increment given the earlier account HTTP 429 response.

## What is persisted

The knowledge tables store document title, text SHA-256, review flag, overlapping text chunks, exact character offsets and corpus revision. References are immutable once registered. Mapping proposals keep the retrieved chunks for auditability. A generated mapping can cite those chunks; this verifies reference identity, not semantic correctness. There is no document edit/delete API in this local increment.

Text uploads are limited to 100,000 characters, chunks default to 1,000 characters with 150-character overlap, retrieval returns at most ten chunks, and the local corpus is capped at 5,000 chunks. The tokenizer is a small English-oriented keyword tokenizer; synonyms and multilingual relevance are known gaps. Search terms are tokenized/quoted rather than treated as raw FTS operators. No arbitrary path or website crawl is exposed.

## Evaluation

Click **Load synthetic demo runbooks**, then **Run regression checks**. The fixed cases live in `samples/evaluation_cases.json` and the three synthetic references in `samples/knowledge/`.

Metrics include recall@3, MRR@3, exact evidence-reference validity, rejection of known injection patterns, one unanswerable-query abstention check and rejection of an invented citation. Results are persisted in `evaluation_runs`. These checks prevent narrow regressions; they are not a production-quality or safety benchmark.

Before promoting RAG to the default, create a representative held-out set of real schemas, reference rules and relevance judgments. Compare the same configured model with and without retrieved context. Measure correct field mappings, unsupported/defaulted fields, abstentions, reviewer corrections, latency and model usage. Do not claim improvement from tests that mock the model.

## API

| Endpoint | Function |
|---|---|
| GET /api/knowledge | List registered references |
| POST /api/knowledge | title, text, trusted, chunk_size, overlap |
| POST /api/knowledge/search | query, top_k → matching source chunks |
| POST /api/knowledge/answer | query, top_k, mode (`extractive` by default, optional `llm`) |
| POST /api/knowledge/demo | Register reviewed synthetic fixtures |
| POST /api/evaluations | Run the fixed local checks |
| GET /api/evaluations | Read recent evaluation results |

`POST /api/mappings/generate` additionally accepts `use_knowledge: true`. All these APIs use the existing authorization checks. The optional `llm` answer mode makes an external model request and fails closed if citation IDs/quotes do not match retrieved text. A real quote does not prove that a generated statement is entailed by it.

## Guardrail limits

Obvious secret-like values and a conservative list of injection patterns are rejected before indexing/querying or sending mapping instructions/profiles. These patterns have false positives and can be evaded. Retain human review, output validation and deterministic execution allowlists. The system does not provide complete PII redaction, general moderation, multi-tenant retrieval access controls or a proof against prompt injection.
