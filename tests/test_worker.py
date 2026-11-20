import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from mle.events import delivery_hash, parse_event
from mle.worker import consume, handle_delivery


def delivery(event_type="company-upserted", data=None):
    return json.dumps(
        {
            "type": event_type,
            "registrationNumber": "SYN-000001",
            "updatedAt": "2026-08-14T09:06:19+08:00",
            "data": data or {},
        }
    ).encode()


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = Mock(ack=AsyncMock(), nack=AsyncMock())
        self.encoder = Mock(model_revision="test-model")
        self.encoder.encode.return_value = [1.0] + [0.0] * 767
        self.cursor = Mock(fetchone=AsyncMock(return_value=None), rowcount=1)
        self.connection = MagicMock(execute=AsyncMock(return_value=self.cursor))
        self.connect = self.enterContext(
            patch("mle.worker.psycopg.AsyncConnection.connect", return_value=self.connection)
        )
        self.sleep = self.enterContext(patch("mle.worker.asyncio.sleep"))

    async def test_broker_timeout_reconnects_after_backoff(self):
        reconnect = self.enterContext(
            patch("mle.worker.consume_connection", side_effect=[TimeoutError(), asyncio.CancelledError()])
        )

        with self.assertRaises(asyncio.CancelledError):
            await consume(self.encoder)

        self.assertEqual(reconnect.await_count, 2)
        self.sleep.assert_awaited_once_with(1)
    async def test_ack_follows_completed_database_write(self):
        self.message.body = delivery(data={"natureOfBusiness": "Tea processing"})

        async def check_commit():
            self.connection.__aexit__.assert_awaited_once()
            self.assertTrue(self.connect.call_args.kwargs["autocommit"])
            self.assertEqual(self.connection.execute.await_count, 2)

        self.message.ack.side_effect = check_commit
        self.assertEqual(await handle_delivery(self.message, self.encoder, 8), 1)
        self.encoder.encode.assert_called_once_with("Tea processing")
        params = self.connection.execute.call_args.args[1]
        self.assertEqual(json.loads(params[-2]), self.encoder.encode.return_value)
        self.assertEqual(params[-1], "test-model")
        self.message.ack.assert_awaited_once_with()
        self.message.nack.assert_not_awaited()

    async def test_invalid_delivery_is_rejected_once_per_body_hash_and_acked(self):
        self.message.body = b"{"
        await handle_delivery(self.message, self.encoder, 1)
        query, params = self.connection.execute.call_args.args
        self.assertIn("ON CONFLICT (body_hash) DO NOTHING", query)
        self.assertEqual(params, (delivery_hash(b"{"), "invalid JSON"))
        self.encoder.encode.assert_not_called()
        self.message.ack.assert_awaited_once_with()

    async def test_duplicate_skips_model_and_write(self):
        self.message.body = delivery(data={"natureOfBusiness": "Tea processing"})
        self.cursor.fetchone.return_value = parse_event(self.message.body).order_key
        await handle_delivery(self.message, self.encoder, 1)
        self.connection.execute.assert_awaited_once()
        self.encoder.encode.assert_not_called()
        self.message.ack.assert_awaited_once_with()

    async def test_tombstone_and_blank_description_clear_vector(self):
        for body, active in (
            (delivery("company-struck-off"), False),
            (delivery(data={"natureOfBusiness": " "}), True),
        ):
            with self.subTest(active=active):
                self.message.body = body
                await handle_delivery(self.message, self.encoder, 1)
                params = self.connection.execute.call_args.args[1]
                self.assertIs(params[6], active)
                self.assertEqual(params[-2:], (None, None))
        self.encoder.encode.assert_not_called()

    async def test_transient_failure_delays_requeue_and_never_acks(self):
        self.message.body = delivery(data={"natureOfBusiness": "Tea processing"})
        self.encoder.encode.side_effect = RuntimeError("server unavailable")
        self.assertEqual(await handle_delivery(self.message, self.encoder, 16), 30)
        self.sleep.assert_awaited_once_with(16)
        self.message.nack.assert_awaited_once_with(requeue=True)
        self.message.ack.assert_not_awaited()
        self.connection.execute.assert_awaited_once()
