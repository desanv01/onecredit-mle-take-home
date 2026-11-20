# Recommendation quality study

The study compares the application's Google EmbeddingGemma 2 rankings with a lexical baseline on eight frozen anchors from the synthetic company feed. The anchor selection and labeling policy are recorded in [anchors.json](anchors.json) and the [relevance rubric](rubric.md).

## Method

- Both rankers use the same snapshot of active companies with nonblank descriptions. The evaluation requires a stored vector from the application's configured model revision for every eligible company. Each search excludes the anchor itself.
- The embedding ranker orders the stored 768-dimensional vectors by exact pgvector cosine distance. The baseline uses cosine similarity on case-folded Unicode word unigrams, with raw term counts and smoothed IDF fitted once over the eligible descriptions. Both rankers use registration number as the secondary sort key.
- The ordinary view keeps every eligible candidate. The second view filters out any candidate whose description exactly matches the anchor after trimming whitespace from both. The filter preserves differences in letter case, punctuation, and wording. Multiple candidates can still share the same description with one another.
- Codex was the sole judge and recorded all 72 labels before opening the rankings. For each anchor, it saw the business descriptions as stored after ingestion. The pool is the union of both rankers' top three in both views, sorted by candidate registration number. Ranker, position, score, and view membership were withheld during labeling.
- Precision@3 is the count of relevant results divided by three for each anchor. Overall precision averages the eight per-anchor values. All ranker and view combinations therefore have 24 result slots, with each anchor weighted equally.

## Results

The reconciled feed had 1,992 active companies. Of those, 1,953 had usable descriptions and stored 768-dimensional vectors from the configured Google model revision. Each ordinary search considered the other 1,952 eligible companies.

| View | EmbeddingGemma 2 | Word-unigram TF-IDF |
| --- | ---: | ---: |
| Ordinary | 100% (24/24) | 100% (24/24) |
| Exclude descriptions identical to the anchor | 100% (24/24) | 25% (6/24) |

In the ordinary view, all 24 slots for each ranker were exact text matches. After excluding those matches, EmbeddingGemma retrieved 19 relevant results in the other language, counting company slots. English and Malay anchors each scored 12/12 for EmbeddingGemma and 3/12 for TF-IDF in that view.

| Frozen anchor | Business activity | EmbeddingGemma relevant out of 3 after exclusion | TF-IDF relevant out of 3 after exclusion |
| --- | --- | ---: | ---: |
| SYN-000202 | Home nursing for older adults | 3 | 0 |
| SYN-000379 | Rubberwood furniture manufacturing | 3 | 0 |
| SYN-000645 | Roads and drainage civil engineering | 3 | 0 |
| SYN-000732 | Cold-chain transport and storage | 3 | 0 |
| SYN-001169 | Cafés and coffee shops | 3 | 3 |
| SYN-001318 | Private kindergarten and childcare | 3 | 3 |
| SYN-001322 | Small-business accounting software | 3 | 0 |
| SYN-001512 | Umrah and inbound travel agency | 3 | 0 |

- For cold-chain transport, EmbeddingGemma found Malay descriptions of transporting and storing frozen goods, including `SYN-000153`. TF-IDF returned frozen-food wholesalers such as `SYN-000033`, which share a product category and provide a different service.
- For the Malay umrah travel agency, EmbeddingGemma found English descriptions of the same travel services, including `SYN-000049`. TF-IDF surfaced freight and customs agencies such as `SYN-000121`.
- Both rankers found relevant cafés in other cities for the Johor Bahru café case. EmbeddingGemma also ranked the Malay description of a Johor Bahru café first in the filtered view.

The filtered results support keeping the multilingual encoder for this feed. The eight anchors were chosen purposively from synthetic templates, and Codex was the sole judge. Six of the eight filtered EmbeddingGemma lists repeat one translated description across all three slots. The 24/24 result therefore measures relevant company slots with substantial text repetition. A follow-up would use independent human reviewers and naturally varied bilingual descriptions, including anchors with no exact or translated copies. Measuring recall or production quality would require additional evidence.

The [labeled pool](pool.json) records the original text and a reason for every decision. The [ranking manifest](rankings.json) records both views, the corpus hash, and the exact model revision.

## Reproduce

Run the full feed with the configured EmbeddingGemma 2 worker. Wait for the queue to drain, then run the following from the repository root with the same database connection settings as the worker.

```console
uv run python -m mle.verify_feed
uv run python -m mle.evaluate pool evaluation/reproduction
```

Open only `evaluation/reproduction/pool.json`. Use the rubric to add an integer `relevant` and a short `reason` to every candidate. Keep the pool's order, IDs, and descriptions intact. For a replay with an identical pool, the recorded judgments can be reused. The scorer verifies the unlabeled pool hash before returning scores.

```console
uv run python -m mle.evaluate score evaluation/reproduction
```

Use a new directory for each fresh pool. Pool generation queries Postgres in a read-only transaction and uses the embeddings already stored by the worker. To recompute the published scores from the included artifacts without Postgres, run `uv run python -m mle.evaluate score evaluation`.
