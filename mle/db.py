"""Connection settings and database initialization for local and CI use."""

import os
from pathlib import Path

import psycopg


def database_url() -> str:
    port = os.environ.get("POSTGRES_PORT", "5432")
    return os.environ.get("DATABASE_URL", f"postgresql://postgres:postgres@127.0.0.1:{port}/companies")


def initialize() -> None:
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url()) as connection:
        connection.execute(schema)


if __name__ == "__main__":
    initialize()
    print("Company and rejection tables are ready.")