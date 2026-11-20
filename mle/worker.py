"""Ingest the registry queue with at-least-once delivery."""

import asyncio
import json
import logging
import os
import sys

import aio_pika
import psycopg
from aio_pika.abc import AbstractIncomingMessage
from aio_pika.exceptions import AMQPConnectionError, ChannelInvalidStateError

from mle.db import database_url
from mle.encoder import GoogleEncoder
from mle.events import InvalidEvent, delivery_hash, parse_event
from producer.publish import QUEUE

LOGGER = logging.getLogger(__name__)
IO_TIMEOUT = 10
MAX_RETRY_DELAY = 30

_CURRENT_ORDER = """
    SELECT event_at, NOT active, body_hash
    FROM companies
    WHERE registration_number = %s
"""

_UPSERT_COMPANY = """
    INSERT INTO companies AS current_company (
        registration_number, company_name, industry, state, nature_of_business,
        event_at, active, body_hash, embedding, model_revision
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s)
    ON CONFLICT (registration_number) DO UPDATE SET
        company_name = EXCLUDED.company_name,
        industry = EXCLUDED.industry,
        state = EXCLUDED.state,
        nature_of_business = EXCLUDED.nature_of_business,
        event_at = EXCLUDED.event_at,
        active = EXCLUDED.active,
        body_hash = EXCLUDED.body_hash,
        embedding = EXCLUDED.embedding,
        model_revision = EXCLUDED.model_revision
    WHERE (EXCLUDED.event_at, NOT EXCLUDED.active, EXCLUDED.body_hash COLLATE "C")
        > (current_company.event_at, NOT current_company.active, current_company.body_hash COLLATE "C")
"""

_INSERT_REJECTION = """
    INSERT INTO rejected_events (body_hash, reason)
    VALUES (%s, %s)
    ON CONFLICT (body_hash) DO NOTHING
"""


class EmbeddingUnavailable(RuntimeError):
    """The embedding server failed before the event was stored."""


async def store_delivery(body: bytes, encoder: GoogleEncoder) -> None:
    """Apply one delivery or record its rejection, then return after commit."""
    async with asyncio.timeout(IO_TIMEOUT):
        connection = await psycopg.AsyncConnection.connect(
            database_url(),
            autocommit=True,
            connect_timeout=5,
            options="-c statement_timeout=5000",
        )

    # Each write is one autocommitted statement. The read before inference avoids
    # repeat work; the conflict predicate still decides ties and concurrent writes.
    async with connection:
        try:
            event = parse_event(body)
        except InvalidEvent as error:
            body_hash = delivery_hash(body)
            async with asyncio.timeout(IO_TIMEOUT):
                cursor = await connection.execute(_INSERT_REJECTION, (body_hash, str(error)))
            if cursor.rowcount:
                LOGGER.warning("Rejected delivery %s: %s", body_hash, error)
            return

        async with asyncio.timeout(IO_TIMEOUT):
            cursor = await connection.execute(_CURRENT_ORDER, (event.registration_number,))
            current_order = await cursor.fetchone()
        if current_order is not None and event.order_key <= current_order:
            return

        vector_text = None
        model_revision = None
        if event.nature_of_business is not None:
            try:
                embedding = await asyncio.to_thread(encoder.encode, event.nature_of_business)
            except RuntimeError as error:
                raise EmbeddingUnavailable(str(error)) from error
            vector_text = json.dumps(embedding, allow_nan=False)
            model_revision = encoder.model_revision

        async with asyncio.timeout(IO_TIMEOUT):
            await connection.execute(
                _UPSERT_COMPANY,
                (
                    event.registration_number, event.company_name, event.industry, event.state,
                    event.nature_of_business, event.event_at, event.active, event.body_hash,
                    vector_text, model_revision,
                ),
            )


async def handle_delivery(message: AbstractIncomingMessage, encoder: GoogleEncoder, retry_delay: int) -> int:
    """Return the next retry delay, starting at one second after each success."""
    retry = False
    try:
        await store_delivery(message.body, encoder)
    except (psycopg.OperationalError, TimeoutError, EmbeddingUnavailable) as error:
        LOGGER.warning("Delivery failed, requeueing in %ds: %s", retry_delay, error)
        # Waiting before requeue prevents an immediate redelivery loop while a
        # dependency is down. Prefetch one bounds the number held by this worker.
        await asyncio.sleep(retry_delay)
        retry = True

    try:
        async with asyncio.timeout(IO_TIMEOUT):
            if retry:
                await message.nack(requeue=True)
            else:
                await message.ack()
    except (AMQPConnectionError, ChannelInvalidStateError):
        # RabbitMQ requeues unsettled deliveries when their channel closes.
        LOGGER.warning("Delivery channel closed before settlement")

    return min(retry_delay * 2, MAX_RETRY_DELAY) if retry else 1


async def consume(encoder: GoogleEncoder) -> None:
    port = os.environ.get("RABBITMQ_PORT", "5672")
    url = os.environ.get("RABBITMQ_URL", f"amqp://guest:guest@localhost:{port}/")
    connection = await aio_pika.connect_robust(url, timeout=IO_TIMEOUT)
    async with connection:
        async with asyncio.timeout(IO_TIMEOUT):
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=1)
            queue = await channel.declare_queue(QUEUE, durable=True)

        LOGGER.info("Consuming %s", QUEUE)
        retry_delay = 1
        async with queue.iterator(no_ack=False) as deliveries:
            async for message in deliveries:
                retry_delay = await handle_delivery(message, encoder, retry_delay)
        raise RuntimeError("Registry queue consumer stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Psycopg asynchronous connections on Windows require a selector loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(consume(GoogleEncoder()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
