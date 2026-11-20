import io
import json
import math
import unittest
from unittest.mock import patch
from urllib.error import URLError

from mle.encoder import DIMENSIONS, MODEL_ID, GoogleEncoder


def response(embedding: list, model: str = MODEL_ID) -> io.BytesIO:
    return io.BytesIO(json.dumps({"model": model, "data": [{"embedding": embedding}]}).encode())


class GoogleEncoderTests(unittest.TestCase):
    def setUp(self) -> None:
        opener = patch("mle.encoder.build_opener")
        self.http = self.enterContext(opener).return_value
        self.encoder = GoogleEncoder()

    def test_symmetric_instruction_normalization_and_cache(self) -> None:
        self.http.open.side_effect = [
            response([3, 4] + [0] * (DIMENSIONS - 2)),
            response([0, 4] + [0] * (DIMENSIONS - 2)),
        ]
        first = self.encoder.encode(" Cafés and bakeries ")
        self.assertEqual(len(first), 768)
        self.assertAlmostEqual(math.hypot(*first), 1)
        self.assertEqual(first[:2], [0.6, 0.8])

        first[0] = 0.0
        self.assertEqual(self.encoder.encode("Cafés and bakeries")[0], 0.6)
        self.encoder.encode("Roasts coffee beans")
        self.assertEqual(self.http.open.call_count, 2)

        for call, text in zip(
            self.http.open.call_args_list, ("Cafés and bakeries", "Roasts coffee beans"), strict=True
        ):
            request = call.args[0]
            self.assertTrue(request.full_url.endswith("/v1/embeddings"))
            self.assertEqual(
                json.loads(request.data),
                {"model": MODEL_ID, "input": f"task: sentence similarity | query: {text}"},
            )
            self.assertEqual(call.kwargs["timeout"], 30)

    def test_blank_description_is_rejected_without_inference(self) -> None:
        with self.assertRaises(ValueError):
            self.encoder.encode(" \r\n ")
        self.http.open.assert_not_called()

    def test_model_identity_dimensions_and_finiteness_are_checked(self) -> None:
        for reply in (
            response([1] * DIMENSIONS, model="wrong-model"),
            response([1]),
            response([0] * DIMENSIONS),
            response([math.nan] + [1] * (DIMENSIONS - 1)),
        ):
            self.http.open.return_value = reply
            with self.assertRaisesRegex(RuntimeError, "invalid embedding"):
                self.encoder.encode("Manufactures electrical components")

    def test_runtime_failure_is_not_cached(self) -> None:
        self.http.open.side_effect = [URLError("offline"), response([1] * DIMENSIONS)]
        with self.assertRaisesRegex(RuntimeError, "could not get an embedding"):
            self.encoder.encode("Logistics")
        self.assertAlmostEqual(math.hypot(*self.encoder.encode("Logistics")), 1)
        self.assertEqual(self.http.open.call_count, 2)
