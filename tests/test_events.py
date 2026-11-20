import json
import unittest
from datetime import UTC, datetime

from mle.events import InvalidEvent, delivery_hash, parse_event


def upsert(**changes: object) -> bytes:
    payload = {
        "type": "company-upserted",
        "registrationNumber": " syn-000521 ",
        "updatedAt": "2026-08-14T09:06:19+08:00",
        "data": {"companyName": "Education Sdn. Bhd.", "state": " KUALA LUMPUR "},
    }
    payload.update(changes)
    return json.dumps(payload).encode()


class EventTests(unittest.TestCase):
    def test_normalizes_identity_and_all_feed_timestamp_forms(self):
        for value in (
            "2026-08-14T09:06:19+08:00",
            "2026-08-14T01:06:19Z",
            "2026-08-14T09:06:19",
            1786669579000,
        ):
            with self.subTest(value=value):
                raw = upsert(updatedAt=value)
                event = parse_event(raw)
                self.assertEqual(event.registration_number, "SYN-000521")
                self.assertEqual(event.event_at, datetime(2026, 8, 14, 1, 6, 19, tzinfo=UTC))
                self.assertEqual(event.state, "Kuala Lumpur")
                self.assertEqual(event.body_hash, delivery_hash(raw))

    def test_nullable_fields_keep_active_snapshot(self):
        for description in (None, "  "):
            with self.subTest(description=description):
                event = parse_event(upsert(data={"natureOfBusiness": description}))
                self.assertTrue(event.active)
                self.assertIsNone(event.nature_of_business)
                self.assertIsNone(event.company_name)
                self.assertIsNone(event.industry)
                self.assertIsNone(event.state)

    def test_tombstone_wins_a_timestamp_tie(self):
        active = parse_event(upsert())
        tombstone = parse_event(upsert(type="company-struck-off", data=None))
        self.assertFalse(tombstone.active)
        self.assertGreater(tombstone.order_key, active.order_key)
        self.assertIsNone(tombstone.nature_of_business)

    def test_permanently_invalid_payloads_report_reason(self):
        for raw, reason in (
            (b"{", "invalid JSON"),
            (b"null", "event must be an object"),
            (upsert(type="new"), "unsupported event type"),
            (upsert(registrationNumber=123), "invalid registrationNumber"),
            (upsert(registrationNumber=None), "missing registrationNumber"),
            (upsert(updatedAt=True), "invalid updatedAt"),
            (upsert(updatedAt="2026-08-14"), "invalid updatedAt"),
            (upsert(data="text"), "upsert data must be an object"),
            (upsert(data={"natureOfBusiness": 123}), "invalid natureOfBusiness"),
            (upsert(data={"industry": "\x00"}), "invalid industry"),
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(InvalidEvent, reason):
                    parse_event(raw)
