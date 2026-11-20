"""HTTP endpoint for company recommendations."""

from typing import Annotated, Literal

import psycopg
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from mle.db import database_url
from mle.events import normalize_registration_number

app = FastAPI(title="Company Recommendations")

# One statement gives the subject and all candidates the same database snapshot.
# The left join preserves the subject when there are no eligible candidates.
RECOMMENDATIONS_SQL = """
WITH subject AS (
    SELECT registration_number, embedding, model_revision,
           embedding IS NOT NULL
           AND coalesce(nature_of_business ~ '[^[:space:]]', false) AS recommendable
    FROM companies
    WHERE registration_number = %s AND active
)
SELECT subject.recommendable,
       candidate.registration_number, candidate.company_name,
       candidate.industry, candidate.state
FROM subject
LEFT JOIN companies AS candidate
    ON subject.recommendable
    AND candidate.active
    AND candidate.registration_number <> subject.registration_number
    AND candidate.embedding IS NOT NULL
    AND candidate.nature_of_business ~ '[^[:space:]]'
    AND candidate.model_revision = subject.model_revision
ORDER BY candidate.embedding <=> subject.embedding, candidate.registration_number
LIMIT %s
"""


class CompanyCard(BaseModel):
    registrationNumber: str
    companyName: str | None
    industry: str | None
    state: str | None


class RecommendationResponse(BaseModel):
    registrationNumber: str
    recommendations: list[CompanyCard]
    reason: Literal["missing_description"] | None = None


@app.get("/companies/{registration_number}/recommendations")
def get_recommendations(
    registration_number: str,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> RecommendationResponse:
    try:
        registration_number = normalize_registration_number(registration_number)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "invalid_registration_number"}
        ) from exc

    with psycopg.connect(database_url(), connect_timeout=3) as connection:
        connection.execute("SET LOCAL statement_timeout = '3s'")
        rows = connection.execute(
            RECOMMENDATIONS_SQL, (registration_number, limit)
        ).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail={"code": "company_not_found"})

    if not rows[0][0]:
        return RecommendationResponse(
            registrationNumber=registration_number,
            recommendations=[],
            reason="missing_description",
        )

    return RecommendationResponse(
        registrationNumber=registration_number,
        recommendations=[
            CompanyCard(
                registrationNumber=candidate_number,
                companyName=company_name,
                industry=industry,
                state=state,
            )
            for _, candidate_number, company_name, industry, state in rows
            if candidate_number is not None
        ],
    )
