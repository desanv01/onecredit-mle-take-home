# OneCredit company recommendations

Find active companies with similar business descriptions. A RabbitMQ worker keeps the latest company state and its Google EmbeddingGemma 2 vector in Postgres. FastAPI returns portal cards using cosine distance in pgvector.

This is a solution to the [OneCredit MLE take-home, version 2026.11](https://github.com/OneCreditMY/mle-take-home/tree/mle-v2026.11). The feed is synthetic. See [DESIGN.md](DESIGN.md) for the failure model, recommendation study, and assumptions.

## Start from a fresh checkout

Install Docker with Compose, [uv](https://docs.astral.sh/uv/) with access to Python 3.14, and a CPU build of [llama.cpp b11527](https://github.com/ggml-org/llama.cpp/releases/tag/b11527). The CPU build must include `llama-server`. Download the pinned text-only Q8_0 model once, to a location outside the repository. On Windows use `curl.exe` and `llama-server.exe`.

```sh
curl -fL -o embeddinggemma-2-Q8_0.gguf https://huggingface.co/ggml-org/embeddinggemma-2-GGUF/resolve/6ea3e3a309d12a4779cfda98b8c19800d2d5da71/embeddinggemma-2-Q8_0.gguf
llama-server --model embeddinggemma-2-Q8_0.gguf --alias google/embeddinggemma-2 --host 127.0.0.1 --port 8081 --embeddings --pooling mean --n-gpu-layers 0 --ctx-size 8192 --batch-size 8192 --ubatch-size 8192 --parallel 1 --threads 4
```

Keep that process running. From the repository root in another terminal:

```sh
uv sync --locked
docker compose up -d --wait
uv run --locked python -m mle.db
uv run --locked python -m mle.encoder "Mengendalikan kafe dan kedai kopi di Lembah Klang"
uv run --locked python -m mle.worker
```

Keep the worker running. In another terminal from the repository root, publish the synthetic feed, then start the API.

```sh
uv run --locked python -m producer.publish
uv run --locked uvicorn mle.api:app --host 127.0.0.1 --port 8080
```

Publishing enqueues work. Wait for the worker to drain the queue before checking the complete feed. RabbitMQ Management is at `http://127.0.0.1:15672` with the starter's local guest credentials. To verify both ready and unacknowledged deliveries, run `docker compose exec -T rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged`. The worker processes one delivery at a time and caches repeated descriptions. The initial feed may take a little while on a laptop.

Use the interactive docs at `http://127.0.0.1:8080/docs` or make a request directly.

```sh
curl -fsS 'http://127.0.0.1:8080/companies/SYN-001550/recommendations?limit=3'
```

The response contains the subject's `registrationNumber` and ranked `recommendations`. Cards contain `registrationNumber`, `companyName`, `industry`, and `state`. A known active company without usable text has an empty list and `reason: "missing_description"`. Unknown and struck-off subjects return 404. Invalid registration numbers and limits return 422.

### Ports and restart

If starter ports are in use, set `POSTGRES_PORT`, `RABBITMQ_PORT`, and `RABBITMQ_UI_PORT` before Compose. Set the corresponding host-side variables in every app terminal too. Example in PowerShell:

```powershell
$env:POSTGRES_PORT = "55482"
$env:RABBITMQ_PORT = "55682"
$env:RABBITMQ_UI_PORT = "15682"
docker compose -p onecredit-mle-assessment up -d --wait
```

`EMBEDDING_PORT` sets the worker's loopback embedding port if you change the model server port. Stop the worker, API, and model server with Ctrl+C. Use `docker compose stop` for a pause that retains state. A fresh disposable run can start under a distinct Compose project and ports. `docker compose down -v` removes that project's volumes.

## Verify the result

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m mle.verify_feed
```

Set `TEST_DATABASE_URL` to an initialized pgvector database to include API SQL checks. Test fixtures use session-local temporary tables. The feed verifier checks the full database against independent expected totals and named source cases. Run it after processing, publish the feed again, and compare the reported snapshot hashes.

The [evaluation guide](evaluation/README.md) describes the optional Google versus TF-IDF study.