# Design

The portal needs companies that do similar work, in the order most likely to be useful. Recommending from the latest accepted business description depends on ingest correctness as much as on the model.

## Event ordering and failure behavior

RabbitMQ is the existing source of changes. The worker declares the supplied durable queue with the same arguments as the producer and uses one unacknowledged delivery at a time. The schema separates current company state from permanent rejections.

- Registration numbers are trimmed and uppercased at both entry points. Nullable business fields are accepted. ISO timestamps with offsets, ISO timestamps without offsets and integer Unix milliseconds occur in the feed. An offset-free timestamp is interpreted as Malaysia time (UTC+8). Compare UTC instants.
- The winning order is event time, strike-off priority, then the SHA-256 body hash for deterministic ties within the same type. An older update cannot overwrite a newer record. A strictly newer valid upsert may reactivate a company. The tie-break is deterministic because the sample provides no external sequence number.
- A strike-off writes a tombstone and removes the vector. An active company without a description stays visible as a known entity without a vector. A newer blank description clears any previous vector. The SQL write applies state, winning hash, vector and model revision together.
- The worker acknowledges after commit. If acknowledgment is lost, the same order key makes redelivery safe. Permanently invalid bodies are recorded once by hash and reason. Transient database or model errors leave the delivery retryable and use capped backoff. A broker timeout tears down the unsettled connection before reconnecting. A pre-read skips encoding for stale events and repeats. The guarded SQL update still resolves concurrent writes.

## Embedding and serving

Use Google EmbeddingGemma 2 text-only at native 768 dimensions, with the pinned Q8_0 artifact and local llama.cpp CPU process. Both descriptions get the same STS instruction. Limit encoder input to 4,096 UTF-8 bytes and retain the full description. The longest supported feed description is 3,359 bytes. The bound leaves space for tokenization and the STS instruction in the model context. Vectors are L2-normalized and stored with model provenance. A bounded in-process cache avoids re-encoding repeated business descriptions. The deployed encoder depends on the local runtime and pinned model artifact; FastAPI and request-time search depend only on the database.

Matching uses business descriptions. State and industry provide card context. An exact cosine search over active vectors from the subject's model revision is small and reproducible at feed size. Registration number orders ties. A single SQL statement reads the subject and candidates in the same snapshot. The API excludes the subject and returns HTTP 404 for a company outside the active set, and HTTP 200 with a reason when a known active company cannot be matched from its description.

## At 10× volume

Measure request latency and EXPLAIN plans over roughly 20,000 active rows with realistic description and update distributions. Exact search remains a correctness reference. If its request cost exceeds the target, test a partial active-vector HNSW index against that reference and report recall@k, latency, index size and build time. Keep model versions separate during migration and backfill the new vectors before switching. Replaying equal-timestamp history intentionally does not perform model migrations.

Measure consumer lag, encode cache hit rate, rejection counts and oldest outstanding delivery age first. Reuse database connections and batch inference if ingest is the bottleneck. Keep the event-order guard when adding consumers. A separate embedding job could improve ingest throughput, with a freshness contract and conditional writes required to ensure an old embedding cannot overwrite the new one.

## Remaining work

This is a local assessment service. Operational roles, hosted deployment, schema migrations across versions, model rollout tooling and persistent service monitoring are future work. The synthetic feed repeats descriptions heavily. Quality and CPU costs measured here apply to this feed and machine. The recommendations rank business-description similarity and do not infer risk, ownership, or creditworthiness.

## AI assistance

Codex assisted with pre-copy assessment research, model and architecture planning, code implementation, tests, documentation and GitHub delivery. GPT‑6.2 Sol Medium worker chats handled the Google encoder, ingestion, FastAPI retrieval and evaluation. The main Codex chat integrated and verified the service against local Docker, the CPU runtime and the public repository. See the evaluation record for how its judgments were produced.
## Verification on the assessment feed

| Check | Observed result |
|---|---|
| Full publish and consume | 2,518 RabbitMQ deliveries. PostgreSQL held 2,000 company records, including 1,992 active and eight struck off. Of the active records, 39 had no embedding. There were 32 distinct rejection records. |
| Named cases | SYN-000286 retained the newer frozen-food wholesale description. SYN-000186 stayed struck off. SYN-001869 returned the missing-description response. |
| Full replay | Republishing all 2,518 events preserved the durable database snapshot hash and the order of all five cards for SYN-001550. |
| Lost acknowledgment | Close a real RabbitMQ channel after the state write commits and before acknowledging. RabbitMQ marked the next delivery as redelivered. Processing it again preserved the database row. |
| CPU and HTTP | Four-thread llama.cpp ran the local Google encoder on a Ryzen 5 3550H. The median across five uncached example embeddings was 113.4 ms. A separate Malay example returned a unit-length vector with 768 components. Ten sequential local API queries against the full database had a median of 85.0 ms, with a range of 73.1–140.0 ms. These are small local smoke samples. |
| Focused checks | 24 tests passed on Python 3.14.4, including real pgvector queries using temporary fixture tables. |

## Optional extension: recommendation quality

The [reproducible study](evaluation/README.md) uses eight frozen anchors, evenly split between English and Malay, and draws both rankers from the same 1,953 eligible companies. Codex judged 72 pooled pairs from the descriptions with ranker and position withheld. Ordinary precision@3 was 24/24 for both Google and word-unigram TF-IDF. After removing descriptions identical to the anchor, EmbeddingGemma 2 found 24/24 relevant company slots and TF-IDF found 6/24.

For the English cold-chain transport anchor, Google matched Malay descriptions of transporting and storing frozen goods, while the lexical baseline matched frozen-food wholesalers. Six of eight filtered Google lists repeated a single translated description across all three company slots. The study supports cross-language matching on this synthetic feed. Human judgments and naturally varied bilingual examples are needed to generalize to production.

## Time box

The public template copy was created on 20 November 2026 at 11:56 a.m. MYT. Read-only research and design planning happened before the copy. Independent implementation lanes ran in parallel during the build. The optional study worker reported 12 minutes elapsed including its wait for ingestion. The recorded Git history and GitHub checks show the delivery sequence.
