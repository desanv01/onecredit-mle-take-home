"""Endpoint tests and optional pgvector checks against isolated temporary rows."""

import os
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import psycopg
from fastapi import HTTPException

from mle import api


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        connector = patch.object(
            api.psycopg, "connect", return_value=nullcontext(self.connection)
        )
        self.connect = connector.start()
        self.addCleanup(connector.stop)

    def test_defaults_normalization_and_card_fields(self):
        self.connection.execute.return_value.fetchall.return_value = [
            (True, "SYN-000002", "Nearest Company", None, "Johor"),
        ]

        response = api.get_recommendations("  syn-000001 ")

        self.connection.execute.assert_called_with(
            api.RECOMMENDATIONS_SQL, ("SYN-000001", 5)
        )
        self.assertEqual(
            response.model_dump(),
            {
                "registrationNumber": "SYN-000001",
                "recommendations": [
                    {
                        "registrationNumber": "SYN-000002",
                        "companyName": "Nearest Company",
                        "industry": None,
                        "state": "Johor",
                    }
                ],
                "reason": None,
            },
        )

    def test_invalid_id_never_connects(self):
        with self.assertRaises(HTTPException) as error:
            api.get_recommendations("   ")
        self.assertEqual(error.exception.status_code, 422)
        self.connect.assert_not_called()

    def test_limits_in_http_contract(self):
        operation = api.app.openapi()["paths"][
            "/companies/{registration_number}/recommendations"
        ]["get"]
        (limit,) = [
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "limit"
        ]
        self.assertEqual(limit["in"], "query")
        self.assertEqual(limit["schema"]["minimum"], 1)
        self.assertEqual(limit["schema"]["maximum"], 50)
        self.assertEqual(limit["schema"]["default"], 5)

    def test_absent_or_inactive_company_is_not_found(self):
        self.connection.execute.return_value.fetchall.return_value = []
        with self.assertRaises(HTTPException) as error:
            api.get_recommendations("SYN-000001")
        self.assertEqual(error.exception.status_code, 404)

    def test_missing_embedding_has_reason(self):
        self.connection.execute.return_value.fetchall.return_value = [
            (False, None, None, None, None),
        ]

        response = api.get_recommendations("SYN-000001")

        self.assertEqual(response.reason, "missing_description")
        self.assertEqual(response.recommendations, [])

    def test_searchable_subject_with_no_neighbors_has_no_reason(self):
        self.connection.execute.return_value.fetchall.return_value = [
            (True, None, None, None, None),
        ]

        response = api.get_recommendations("SYN-000001")

        self.assertIsNone(response.reason)
        self.assertEqual(response.recommendations, [])


@unittest.skipUnless(
    os.environ.get("TEST_DATABASE_URL"),
    "set TEST_DATABASE_URL to an initialized pgvector database",
)
class PgvectorRecommendationTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(
            os.environ["TEST_DATABASE_URL"],
            connect_timeout=3,
            options="-c statement_timeout=3000",
        )
        self.addCleanup(self.connection.close)
        self.connection.execute(
            "CREATE TEMP TABLE companies (LIKE public.companies INCLUDING ALL)"
        )
        connector = patch.object(
            api.psycopg, "connect", return_value=nullcontext(self.connection)
        )
        connector.start()
        self.addCleanup(connector.stop)

    def add_company(
        self,
        number,
        coordinates=None,
        revision="model@one",
        *,
        active=True,
        description="Business activities",
    ):
        vector = None
        if coordinates is not None:
            padded = list(coordinates) + [0] * (768 - len(coordinates))
            vector = "[" + ",".join(map(str, padded)) + "]"
        self.connection.execute(
            """
            INSERT INTO companies (
                registration_number, company_name, industry, state, nature_of_business,
                event_at, active, body_hash, embedding, model_revision
            )
            VALUES (%s, %s, 'Services', 'Johor', %s, now(), %s, 'test', %s::vector, %s)
            """,
            (
                number,
                f"Company {number}",
                description,
                active,
                vector,
                revision if vector is not None else None,
            ),
        )

    def test_exact_cosine_tie_break_revision_and_limit(self):
        self.add_company("SYN-000001", [1, 0])
        self.add_company("SYN-000003", [2, 0])
        self.add_company("SYN-000002", [1, 0])
        self.add_company("SYN-000004", [100, 1])
        self.add_company("SYN-000005", [1, 0], "model@two")
        self.add_company("SYN-000006", active=False)
        self.add_company("SYN-000007")
        self.add_company("SYN-000008", [1, 0], description="\t\n")

        result = api.get_recommendations("SYN-000001")

        self.assertEqual(
            [card.registrationNumber for card in result.recommendations],
            ["SYN-000002", "SYN-000003", "SYN-000004"],
        )
        limited = api.get_recommendations("SYN-000001", limit=1)
        self.assertEqual(
            [card.registrationNumber for card in limited.recommendations], ["SYN-000002"]
        )

    def test_active_company_with_blank_description_or_missing_vector(self):
        self.add_company("SYN-000001", description=" \t\n ")
        self.add_company("SYN-000002")
        self.add_company("SYN-000003", [1, 0], description="\t\n")
        for number in ("SYN-000001", "SYN-000002", "SYN-000003"):
            with self.subTest(number=number):
                result = api.get_recommendations(number)
                self.assertEqual(result.recommendations, [])
                self.assertEqual(result.reason, "missing_description")

    def test_inactive_and_absent_company(self):
        self.add_company("SYN-000001", active=False)
        for number in ("SYN-000001", "SYN-000002"):
            with self.subTest(number=number):
                with self.assertRaises(HTTPException) as error:
                    api.get_recommendations(number)
                self.assertEqual(error.exception.status_code, 404)
