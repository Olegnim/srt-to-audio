"""Тесты загрузки и проверки настроек."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from srt_to_audio.config import ConfigError, load_settings, parse_rate_percent


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _load(self, payload: dict) -> object:
        path = self.root / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_settings(path)

    def test_defaults(self) -> None:
        settings = self._load({})
        self.assertEqual(settings.voice.name, "ru-RU-DmitryNeural")
        self.assertEqual(settings.processing.request_concurrency, 3)

    def test_short_rate_without_sign_is_rejected(self) -> None:
        # edge-tts принимает только "+10%"; без знака запрос падал бы на синтезе.
        with self.assertRaises(ConfigError) as ctx:
            self._load({"voice": {"rate": "10%"}})
        self.assertIn("voice.rate", str(ctx.exception))

    def test_bad_pitch(self) -> None:
        with self.assertRaises(ConfigError):
            self._load({"voice": {"pitch": "-20%"}})

    def test_wrong_type_gives_config_error(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            self._load({"voice": {"name": 123}})
        self.assertIn("voice.name", str(ctx.exception))

    def test_unknown_key(self) -> None:
        with self.assertRaises(ConfigError):
            self._load({"processing": {"nope": 1}})

    def test_concurrency_bounds(self) -> None:
        with self.assertRaises(ConfigError):
            self._load({"processing": {"request_concurrency": 0}})
        with self.assertRaises(ConfigError):
            self._load({"processing": {"request_concurrency": 17}})

    def test_parse_rate_percent(self) -> None:
        self.assertEqual(parse_rate_percent("+35%"), 35)
        self.assertEqual(parse_rate_percent("-20%"), -20)


if __name__ == "__main__":
    unittest.main()
