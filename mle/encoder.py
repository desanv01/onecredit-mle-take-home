"""Local text embeddings from Google's EmbeddingGemma 2."""

import argparse
import json
import math
import os
from functools import lru_cache
from http.client import HTTPException
from time import perf_counter
from urllib.request import ProxyHandler, Request, build_opener

MODEL_ID = "google/embeddinggemma-2"
GGUF_REVISION = "6ea3e3a309d12a4779cfda98b8c19800d2d5da71"
DIMENSIONS = 768
MAX_DESCRIPTION_BYTES = 4096
STS_PREFIX = "task: sentence similarity | query: "


class GoogleEncoder:
    """Encode both sides of a similarity comparison with the same STS prompt."""

    model_revision = f"{MODEL_ID}:ggml-org/Q8_0@{GGUF_REVISION}:sts"

    def __init__(self) -> None:
        port = int(os.environ.get("EMBEDDING_PORT", "8081"))
        self._url = f"http://127.0.0.1:{port}/v1/embeddings"
        self._http = build_opener(ProxyHandler({}))
        self._encode_cached = lru_cache(maxsize=512)(self._encode_uncached)

    def encode(self, description: str) -> list[float]:
        text = description.strip()
        if not text:
            raise ValueError("description must contain non-whitespace text")
        # Bound inference input well below the server context. The source text
        # remains intact in Postgres. Drop a partial code point at the boundary.
        bounded = text.encode("utf-8")[:MAX_DESCRIPTION_BYTES].decode("utf-8", errors="ignore")
        return list(self._encode_cached(bounded))

    def _encode_uncached(self, text: str) -> tuple[float, ...]:
        # llama.cpp's embedding endpoint takes raw text and adds no task prompt.
        body = json.dumps({"model": MODEL_ID, "input": STS_PREFIX + text}).encode("utf-8")
        request = Request(self._url, data=body, headers={"Content-Type": "application/json"})
        try:
            with self._http.open(request, timeout=30) as response:
                result = json.loads(response.read(65_536))
        except (OSError, HTTPException) as exc:
            raise RuntimeError(
                f"could not get an embedding from llama.cpp at {self._url}: {exc}"
            ) from exc
        except ValueError as exc:
            raise RuntimeError("llama.cpp returned an invalid JSON response") from exc

        try:
            if result["model"] != MODEL_ID or len(result["data"]) != 1:
                raise ValueError("unexpected model or result count")
            embedding = result["data"][0]["embedding"]
            if (
                not isinstance(embedding, list)
                or len(embedding) != DIMENSIONS
                or any(type(value) not in (int, float) for value in embedding)
            ):
                raise ValueError("expected 768 numeric components")
            norm = math.hypot(*embedding)
            if not math.isfinite(norm) or norm == 0:
                raise ValueError("expected a finite, nonzero embedding")
        except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"llama.cpp returned an invalid embedding: {exc}") from exc

        return tuple(value / norm for value in embedding)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the local EmbeddingGemma 2 server.")
    parser.add_argument("description", nargs="?", default="Operating cafes and coffee shops.")
    args = parser.parse_args()

    encoder = GoogleEncoder()
    started = perf_counter()
    embedding = encoder.encode(args.description)
    elapsed_ms = 1000 * (perf_counter() - started)
    print(
        f"{encoder.model_revision} | dimensions={len(embedding)} "
        f"| norm={math.hypot(*embedding):.8f} | elapsed_ms={elapsed_ms:.1f}"
    )


if __name__ == "__main__":
    main()
