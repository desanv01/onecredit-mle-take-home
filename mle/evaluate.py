"""Pool and score an optional, read-only company recommendation study."""

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import psycopg

from mle.db import database_url
from mle.encoder import GoogleEncoder

ANCHORS = Path(__file__).resolve().parents[1] / "evaluation" / "anchors.json"
K = 3
TOKEN = re.compile(r"[^\W_]+")
RANKERS = ("embedding", "tfidf")
VIEWS = ("ordinary", "exclude_identical")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def tfidf_vectors(descriptions):
    """Fit smoothed IDF on the shared candidate corpus, with raw term counts."""
    counts = {
        company_id: Counter(TOKEN.findall(description.casefold()))
        for company_id, description in descriptions.items()
    }
    document_frequency = Counter(word for terms in counts.values() for word in terms)
    idf = {
        word: 1 + math.log((1 + len(counts)) / (1 + frequency))
        for word, frequency in document_frequency.items()
    }
    vectors = {}
    for company_id, terms in counts.items():
        weights = {word: count * idf[word] for word, count in terms.items()}
        norm = math.hypot(*weights.values())
        vectors[company_id] = {word: weight / norm for word, weight in weights.items()}
    return vectors


def tfidf_ranking(anchor_id, vectors):
    query = vectors[anchor_id]
    return sorted(
        (company_id for company_id in vectors if company_id != anchor_id),
        key=lambda company_id: (
            -math.fsum(weight * vectors[company_id].get(word, 0) for word, weight in query.items()),
            company_id,
        ),
    )


def stored_rankings(anchors, revision):
    # Use one consistent snapshot while ingestion or a backfill may be running.
    with psycopg.connect(database_url()) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        rows = connection.execute(
            """
            SELECT registration_number, nature_of_business, model_revision
            FROM companies
            WHERE active
            ORDER BY registration_number COLLATE "C"
            """
        ).fetchall()
        descriptions = {
            company_id: description
            for company_id, description, _ in rows
            if description and description.strip()
        }
        for anchor in anchors:
            if descriptions.get(anchor["id"]) != anchor["description"]:
                raise ValueError(f"Database description does not match frozen anchor {anchor['id']}")
        revisions = Counter(
            row_revision for company_id, _, row_revision in rows if company_id in descriptions
        )
        if revisions != {revision: len(descriptions)}:
            raise ValueError(
                f"Wait for every active nonblank description to have revision {revision}. "
                f"Observed revision counts: {dict(revisions)}"
            )
        # Materialize candidates so this is an exact scan even if an ANN index exists.
        rankings = {}
        for anchor in anchors:
            candidates = connection.execute(
                """
                WITH candidates AS MATERIALIZED (
                    SELECT registration_number, embedding
                    FROM companies
                    WHERE active AND model_revision = %s
                )
                SELECT registration_number, embedding <=> (
                    SELECT embedding FROM candidates WHERE registration_number = %s
                ) AS distance
                FROM candidates
                WHERE registration_number <> %s
                ORDER BY distance, registration_number COLLATE "C"
                """,
                (revision, anchor["id"], anchor["id"]),
            )
            ranking = []
            for company_id, distance in candidates:
                if company_id in descriptions:
                    if distance is None or not math.isfinite(distance):
                        raise ValueError(f"Invalid cosine distance for {anchor['id']} / {company_id}")
                    ranking.append(company_id)
            if len(ranking) != len(descriptions) - 1:
                raise ValueError(f"Incomplete candidate ranking for {anchor['id']}")
            rankings[anchor["id"]] = ranking
    return descriptions, rankings, len(rows)


def top_k_views(ranked_ids, description, descriptions):
    stripped = description.strip()
    distinct = [
        company_id for company_id in ranked_ids if descriptions[company_id].strip() != stripped
    ]
    if len(distinct) < K:
        raise ValueError("Fewer than three candidates remain after removing exact copies")
    return {"ordinary": ranked_ids[:K], "exclude_identical": distinct[:K]}


def make_pool(anchors, descriptions, embeddings):
    vectors = tfidf_vectors(descriptions)
    pool = []
    rankings = {}
    for anchor in anchors:
        anchor_id = anchor["id"]
        rankings[anchor_id] = {
            "embedding": top_k_views(embeddings[anchor_id], anchor["description"], descriptions),
            "tfidf": top_k_views(tfidf_ranking(anchor_id, vectors), anchor["description"], descriptions),
        }
        pooled_ids = {
            company_id
            for ranker in rankings[anchor_id].values()
            for top_ids in ranker.values()
            for company_id in top_ids
        }
        pool.append({
            "anchor": anchor,
            "candidates": [
                {"id": company_id, "description": descriptions[company_id]}
                for company_id in sorted(pooled_ids)
            ],
        })
    return pool, rankings


def create_study(output_dir):
    anchors = read_json(ANCHORS)
    revision = GoogleEncoder.model_revision
    descriptions, embedding_rankings, active_count = stored_rankings(anchors, revision)
    pool, rankings = make_pool(anchors, descriptions, embedding_rankings)
    output_dir.mkdir(parents=True, exist_ok=True)
    pool_path = output_dir / "pool.json"
    rankings_path = output_dir / "rankings.json"
    if pool_path.exists() or rankings_path.exists():
        raise ValueError("Choose a fresh output directory to preserve existing study files")
    write_json(pool_path, pool)
    write_json(rankings_path, {
        "model_revision": revision,
        "active_companies": active_count,
        "eligible_companies_including_anchors": len(descriptions),
        "description_corpus_sha256": fingerprint(descriptions),
        "unlabeled_pool_sha256": fingerprint(pool),
        "rankings": rankings,
    })
    print(f"Judge {sum(len(case['candidates']) for case in pool)} pairs in {pool_path}.")
    print("Keep rankings.json closed until all labels have been entered.")


def score_study(pool, manifest):
    unlabeled_pool = []
    cases = []
    for case in pool:
        anchor = case["anchor"]
        labels = {candidate["id"]: candidate for candidate in case["candidates"]}
        for candidate in labels.values():
            value = candidate.get("relevant")
            reason = candidate.get("reason")
            if type(value) is not int or value not in (0, 1) or not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"Missing binary label or reason for {anchor['id']} / {candidate['id']}")
        unlabeled_pool.append({
            "anchor": anchor,
            "candidates": [
                {"id": candidate["id"], "description": candidate["description"]}
                for candidate in case["candidates"]
            ],
        })
        results = {}
        for ranker in RANKERS:
            results[ranker] = {}
            for view in VIEWS:
                top_ids = manifest["rankings"][anchor["id"]][ranker][view]
                if len(set(top_ids)) != K or len(top_ids) != K:
                    raise ValueError(f"Invalid top {K} for {anchor['id']} / {ranker} / {view}")
                top = [labels[company_id] for company_id in top_ids]
                results[ranker][view] = {
                    "relevant": sum(candidate["relevant"] for candidate in top),
                    "exact_copies": sum(
                        candidate["description"].strip() == anchor["description"].strip()
                        for candidate in top
                    ),
                }
        cases.append({"anchor_id": anchor["id"], "language": anchor["language"], "counts": results})
    if fingerprint(unlabeled_pool) != manifest["unlabeled_pool_sha256"]:
        raise ValueError("Pool membership, order, or text changed since ranking")

    summaries = {}
    for language in ("all", "English", "Malay"):
        subset = [case for case in cases if language == "all" or case["language"] == language]
        slots = K * len(subset)
        summaries[language] = {}
        for ranker in RANKERS:
            summaries[language][ranker] = {}
            for view in VIEWS:
                relevant = sum(case["counts"][ranker][view]["relevant"] for case in subset)
                summaries[language][ranker][view] = {
                    "relevant": relevant,
                    "slots": slots,
                    "precision_at_3": relevant / slots,
                    "exact_copies": sum(case["counts"][ranker][view]["exact_copies"] for case in subset),
                }
    return {"summary": summaries, "cases": cases}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pool = commands.add_parser("pool", help="Read stored embeddings and create the blind judging pool")
    pool.add_argument("directory", type=Path, help="Fresh output directory")
    score = commands.add_parser("score", help="Reveal ranker results from a fully judged pool")
    score.add_argument("directory", type=Path, help="Directory with judged pool.json and rankings.json")
    args = parser.parse_args()
    try:
        if args.command == "pool":
            create_study(args.directory)
        else:
            result = score_study(
                read_json(args.directory / "pool.json"),
                read_json(args.directory / "rankings.json"),
            )
            print(json.dumps(result, indent=2))
    except (ValueError, OSError, psycopg.Error) as exc:
        parser.exit(1, f"Evaluation failed: {exc}\n")


if __name__ == "__main__":
    main()
