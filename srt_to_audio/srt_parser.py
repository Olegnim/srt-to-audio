"""Чтение и разбор файлов субтитров формата SRT."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1251")

_TIMING_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)
_INDEX_RE = re.compile(r"^\d+$")
_TIMESTAMP_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$")
_MARKUP_RE = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}|\{[a-zA-Z][^}]*\}")


class SrtError(Exception):
    """Файл субтитров отсутствует, не читается или имеет неверный формат."""


@dataclass(frozen=True)
class Cue:
    """Одна реплика субтитров."""

    index: int
    start: str
    end: str
    text: str


def read_srt(path: Path) -> str:
    """Читает файл субтитров, подбирая кодировку."""
    if not path.is_file():
        raise SrtError(f"Файл субтитров не найден: {path}")

    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
        except OSError as exc:
            raise SrtError(f"Не удалось прочитать {path}: {exc}") from exc

    raise SrtError(f"Не удалось определить кодировку файла {path}: {last_error}")


def parse_srt(content: str) -> list[Cue]:
    """Разбирает содержимое SRT в список реплик."""
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[Cue] = []
    index = 0
    start = ""
    end = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal index, start, end, buffer
        if start and buffer:
            text = " ".join(part for part in buffer if part)
            if text:
                cues.append(Cue(index=index, start=start, end=end, text=text))
        index = 0
        start = ""
        end = ""
        buffer = []

    for raw_line in lines:
        line = raw_line.strip()
        timing = _TIMING_RE.search(line)
        if timing:
            if start or buffer:
                flush()
            start, end = timing.group(1), timing.group(2)
            continue
        if not line:
            flush()
            continue
        if not start and not buffer and _INDEX_RE.match(line):
            index = int(line)
            continue
        buffer.append(_clean(line))

    flush()

    if not cues:
        raise SrtError("В файле не найдено ни одной реплики субтитров.")
    return cues


def timestamp_seconds(value: str) -> float:
    """Переводит метку SRT ("00:01:02,500") в секунды."""
    match = _TIMESTAMP_RE.match(value.strip())
    if not match:
        raise ValueError(f"Неверная метка времени: {value!r}")
    hours, minutes, seconds, fraction = match.groups()
    # Дробная часть — это доли секунды, а не десятичная запись: "5" в метке
    # "00:00:01,5" означает 0,5 с только в формате с тремя знаками (",500").
    # Поэтому делим на 10 в степени числа знаков, а не подставляем "0.{fraction}".
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction) / 10 ** len(fraction)
    )


def timeline_seconds(cues: Iterable[Cue]) -> float:
    """Длительность субтитров: время окончания последней реплики.

    Это ориентир для озвучки: если mp3 длиннее, речь не укладывается в таймкод.
    """
    ends: list[float] = []
    for cue in cues:
        try:
            ends.append(timestamp_seconds(cue.end))
        except ValueError:
            continue
    return max(ends) if ends else 0.0


def _clean(line: str) -> str:
    """Убирает разметку субтитров (теги и ASS-команды) и лишние пробелы."""
    return _MARKUP_RE.sub("", line).strip()
