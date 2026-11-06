"""配信用モデルのJSON変換と、汎用的な取得値の検証を確認する。"""

import json
import unittest
from datetime import datetime

from pydantic import ValidationError

from backend.app.models.events import CameraPreviewEvent, MeasurementEvent


class MeasurementEventTests(unittest.TestCase):
    def make_event(self, **changes):
        values = {
            "server_instance_id": "server-test", "measurement_id": "測定A",
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

    def test_ids_are_required_and_must_not_be_null_or_empty(self):
        for key in ("server_instance_id", "measurement_id"):
            for value in (None, ""):
                with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                    self.make_event(**{key: value})
            payload = self.make_event().model_dump()
            del payload[key]
            with self.assertRaises(ValidationError):
                MeasurementEvent.model_validate(payload)

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


class CameraPreviewEventTests(unittest.TestCase):
    def make_event(self, **changes):
        values = {
            "server_instance_id": "server-test", "measurement_id": "測定A",
            "id": "カメラA", "frame_index": 7, "timestamp_ns": 10**18 + 123,
            "width": 2, "height": 2, "jpeg": b"\xff\xd8\xff\xd9",
        }
        values.update(changes)
        return CameraPreviewEvent(**values)

    def test_binary_message_contains_byte_length_metadata_and_original_jpeg(self):
        event = self.make_event()

        message = event.to_bytes()

        header_length = int.from_bytes(message[:4], "big")
        header = message[4:4 + header_length]
        self.assertEqual(len(message) - 4 - len(event.jpeg), header_length)
        self.assertGreater(len(header), len(header.decode("utf-8")))
        self.assertEqual({
            "type": "camera_preview", "id": event.id, "frame_index": 7,
            "server_instance_id": event.server_instance_id, "measurement_id": event.measurement_id,
            "timestamp_ns": str(event.timestamp_ns), "width": 2, "height": 2,
        }, json.loads(header))
        self.assertEqual(event.jpeg, message[4 + header_length:])

    def test_python_dictionary_keeps_jpeg_bytes_and_integer_timestamp_for_publishing(self):
        event = self.make_event()
        payload = event.model_dump()

        self.assertIs(event.jpeg, payload["jpeg"])
        self.assertEqual(event.timestamp_ns, payload["timestamp_ns"])
        self.assertEqual(event, CameraPreviewEvent.model_validate(payload))

    def test_ids_are_required_and_must_not_be_null_or_empty(self):
        for key in ("server_instance_id", "measurement_id"):
            for value in (None, ""):
                with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                    self.make_event(**{key: value})
            payload = self.make_event().model_dump()
            del payload[key]
            with self.assertRaises(ValidationError):
                CameraPreviewEvent.model_validate(payload)

    def test_invalid_metadata_and_missing_jpeg_are_rejected(self):
        for changes in (
            {"id": ""}, {"frame_index": -1}, {"timestamp_ns": -1},
            {"width": 0}, {"height": 0}, {"jpeg": b""}, {"jpeg": "not-bytes"},
            {"type": "measurement"}, {"extra": 1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.make_event(**changes)
        with self.assertRaises(ValidationError):
            CameraPreviewEvent.model_validate({"id": "camera"})


if __name__ == "__main__":
    unittest.main()
