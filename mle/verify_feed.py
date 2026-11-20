"""Check the supplied feed's durable state independently of the consumer."""

import hashlib
import json

import psycopg

from mle.db import database_url


def main() -> None:
    with psycopg.connect(database_url()) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        totals = connection.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE active),
                   count(*) FILTER (WHERE NOT active),
                   count(*) FILTER (WHERE active AND embedding IS NULL)
            FROM companies
            """
        ).fetchone()
        rejected = connection.execute("SELECT count(*) FROM rejected_events").fetchone()[0]
        named = dict(connection.execute(
            """SELECT registration_number, nature_of_business FROM companies
               WHERE registration_number IN ('SYN-000286', 'SYN-001869')"""
        ))
        strike_off = connection.execute(
            "SELECT active, embedding IS NULL FROM companies WHERE registration_number = 'SYN-000186'"
        ).fetchone()
        snapshot = hashlib.sha256()
        for (row_hash,) in connection.execute(
            "SELECT md5(to_jsonb(c)::text) FROM companies c ORDER BY registration_number"
        ):
            snapshot.update(row_hash.encode())
        for (row_hash,) in connection.execute(
            "SELECT md5(to_jsonb(r)::text) FROM rejected_events r ORDER BY body_hash"
        ):
            snapshot.update(row_hash.encode())

    result = {
        "companies": totals[0],
        "active": totals[1],
        "struck_off": totals[2],
        "active_without_embedding": totals[3],
        "distinct_rejections": rejected,
        "snapshot_sha256": snapshot.hexdigest(),
    }
    print(json.dumps(result, indent=2))
    if totals != (2000, 1992, 8, 39):
        raise SystemExit(f"Unexpected company totals: {totals}")
    if rejected != 32:
        raise SystemExit(f"Unexpected distinct rejection total: {rejected}")
    if "frozen" not in (named.get("SYN-000286") or "").casefold():
        raise SystemExit("SYN-000286 did not retain its frozen-food wholesale update")
    if "SYN-001869" not in named or (named["SYN-001869"] or "").strip():
        raise SystemExit("SYN-001869 is absent or unexpectedly has a nonblank description")
    if strike_off != (False, True):
        raise SystemExit("SYN-000186 did not retain the strike-off tombstone")


if __name__ == "__main__":
    main()