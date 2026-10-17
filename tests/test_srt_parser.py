"""Тесты разбора SRT и меток времени."""

from __future__ import annotations

import unittest

from srt_to_audio.srt_parser import (
    Cue,
    SrtError,
    parse_srt,
    timeline_seconds,
    timestamp_seconds,
)

SAMPLE = """1
00:00:01,000 --> 00:00:03,500
<i>Привет</i>, мир!

2
00:00:04,000 --> 00:00:06,000
{\\an8}Вторая реплика
"""


class TimestampTest(unittest.TestCase):
    def test_three_digit_fraction(self) -> None:
        self.assertAlmostEqual(timestamp_seconds("00:01:02,500"), 62.5)

    def test_short_fraction_is_a_fraction_of_a_second(self) -> None:
        # Стандарт SRT — три знака (",500"), но встречаются ",5" и ",05":
        # это доли секунды, а не целые десятые.
        self.assertAlmostEqual(timestamp_seconds("00:00:01,05"), 1.05)
        self.assertAlmostEqual(timestamp_seconds("00:00:01,5"), 1.5)
        self.assertAlmostEqual(timestamp_seconds("00:00:01,005"), 1.005)

    def test_dot_separator(self) -> None:
        self.assertAlmostEqual(timestamp_seconds("00:00:02.250"), 2.25)

    def test_bad_value(self) -> None:
        with self.assertRaises(ValueError):
            timestamp_seconds("не метка")


class ParseSrtTest(unittest.TestCase):
    def test_cues_and_markup(self) -> None:
        cues = parse_srt(SAMPLE)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start, "00:00:01,000")
        self.assertEqual(cues[0].text, "Привет, мир!")
        self.assertEqual(cues[1].text, "Вторая реплика")
        self.assertEqual([cue.index for cue in cues], [1, 2])

    def test_crlf(self) -> None:
        cues = parse_srt(SAMPLE.replace("\n", "\r\n"))
        self.assertEqual(len(cues), 2)

    def test_empty_file_raises(self) -> None:
        with self.assertRaises(SrtError):
            parse_srt("\n\n")

    def test_timeline_is_last_end(self) -> None:
        cues = [Cue(index=1, start="00:00:00,000", end="00:00:09,000", text="а")]
        self.assertAlmostEqual(timeline_seconds(cues), 9.0)


if __name__ == "__main__":
    unittest.main()
