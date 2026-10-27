"""配信用モデルのJSON変換と、汎用的な取得値の検証を確認する。"""

import json
import unittest
from datetime import datetime

from pydantic import ValidationError

from backend.app.models.events import MeasurementEvent


class MeasurementEventTests(unittest.TestCase):
    def make_event(self, **changes):
        values = {
            "id": "sensor_1",
            "timestamp_ns": 123456789,
            "elapsed_s": 1.25,
            "datetime": datetime.fromisoformat("2026-10-27T12:00:01.250000+09:00"),
            "data": {"temperature_c": 23.5, "channels": [1, 2], "status": {"valid": True}},
        }
        values.update(changes)
        return MeasurementEvent(**values)

    def test_dump_is_json_compatible_and_round_trips(self):
        event = self.make_event()
        payload = event.model_dump(mode="json")

        self.assertEqual("measurement", payload["type"])
        self.assertEqual(event.datetime.isoformat(), payload["datetime"])
        self.assertEqual(event.data, payload["data"])
        self.assertEqual(event, MeasurementEvent.model_validate_json(json.dumps(payload)))

    def test_non_json_payload_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_event(data={"unsupported": object()})

    def test_other_event_type_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.make_event(type="settings")

    def test_dump_is_independent_of_later_nested_changes(self):
        event = self.make_event()
        payload = event.model_dump(mode="json")

        event.data["channels"].append(3)
        event.data["status"]["valid"] = False

        self.assertEqual([1, 2], payload["data"]["channels"])
        self.assertTrue(payload["data"]["status"]["valid"])


if __name__ == "__main__":
    unittest.main()
