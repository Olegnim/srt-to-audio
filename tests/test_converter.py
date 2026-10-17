"""Тесты сборки фрагментов и путей результата."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from srt_to_audio.config import load_settings
from srt_to_audio.converter import (
    build_fragments,
    count_words,
    output_path_for,
    split_text,
)
from srt_to_audio.srt_parser import Cue


def _cue(index: int, start: str, end: str, text: str) -> Cue:
    return Cue(index=index, start=start, end=end, text=text)


class BuildFragmentsTest(unittest.TestCase):
    def test_cues_are_merged_into_block(self) -> None:
        cues = [
            _cue(1, "00:00:00,000", "00:00:02,000", "Первая."),
            _cue(2, "00:00:02,000", "00:00:04,000", "Вторая."),
        ]
        fragments = build_fragments(cues, max_chars=3000, block_max_seconds=12)
        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0].text, "Первая. Вторая.")
        self.assertEqual(fragments[0].pauses, ())

    def test_long_gap_splits_block(self) -> None:
        cues = [
            _cue(1, "00:00:00,000", "00:00:02,000", "Первая."),
            _cue(2, "00:00:10,000", "00:00:12,000", "Вторая."),
        ]
        fragments = build_fragments(
            cues, max_chars=3000, block_max_seconds=12, gap_max_seconds=2
        )
        self.assertEqual(len(fragments), 2)

    def test_short_gap_is_remembered_as_pause(self) -> None:
        cues = [
            _cue(1, "00:00:00,000", "00:00:02,000", "Первая."),
            _cue(2, "00:00:03,000", "00:00:05,000", "Вторая."),
        ]
        fragments = build_fragments(
            cues, max_chars=3000, block_max_seconds=12, gap_max_seconds=2
        )
        self.assertEqual(len(fragments), 1)
        self.assertEqual(len(fragments[0].pauses), 1)
        # Пауза ставится перед первым словом второй реплики.
        self.assertEqual(fragments[0].pauses[0].before_word, count_words("Первая."))

    def test_block_max_seconds_zero_keeps_cues_apart(self) -> None:
        cues = [
            _cue(1, "00:00:00,000", "00:00:02,000", "Раз."),
            _cue(2, "00:00:02,000", "00:00:04,000", "Два."),
        ]
        fragments = build_fragments(cues, max_chars=3000, block_max_seconds=0)
        self.assertEqual(len(fragments), 2)

    def test_long_block_is_split_by_chars(self) -> None:
        cues = [_cue(1, "00:00:00,000", "00:00:10,000", "Слово " * 200)]
        fragments = build_fragments(cues, max_chars=200, block_max_seconds=12)
        self.assertGreater(len(fragments), 1)
        for fragment in fragments:
            self.assertLessEqual(len(fragment.text), 200)


class SplitTextTest(unittest.TestCase):
    def test_splits_by_sentences(self) -> None:
        chunks = split_text("Первое. Второе. Третье.", 20)
        self.assertEqual(chunks, ["Первое. Второе.", "Третье."])

    def test_splits_long_sentence_by_words(self) -> None:
        chunks = split_text("ааа ббб ввв ггг ддд", 9)
        self.assertTrue(all(len(chunk) <= 9 for chunk in chunks))
        self.assertEqual(" ".join(chunks), "ааа ббб ввв ггг ддд")


class OutputPathTest(unittest.TestCase):
    def test_out_name_cannot_escape_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text('{"paths": {"output_dir": "."}}', encoding="utf-8")
            settings = load_settings(config)
            target = output_path_for(Path("input/movie.srt"), settings, "../evil")
            self.assertEqual(target.parent, Path(tmp).resolve())
            self.assertEqual(target.name, "evil.mp3")


if __name__ == "__main__":
    unittest.main()
