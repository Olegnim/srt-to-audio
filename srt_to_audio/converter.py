"""Преобразование SRT-файла в mp3-дорожку."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Protocol, Sequence

from .config import Settings, VoiceConfig, format_rate_percent
from .mp3 import duration_seconds
from .srt_parser import Cue, cues_to_text, parse_srt, read_srt, timeline_seconds
from .tts import TtsError, synthesize

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])[\s\n]+")

# Подгонка под таймкод: расхождение до 2 % считаем нормой (это доли секунды
# на длинном файле), переозвучиваем не больше двух раз — обычно хватает одного.
_FIT_TOLERANCE = 0.02
_FIT_ATTEMPTS = 2
# Замедление ограничено: на -100 % синтезатор просто останавливается, поэтому
# дальше -50 % речь не замедляем (как и разрешает edge-tts).
_MIN_RATE_PERCENT = -50


class ConverterError(Exception):
    """Проблемы при подготовке исходных данных или записи результата."""


class Reporter(Protocol):
    """Интерфейс отображения процесса (реализуется в CLI)."""

    def stage(self, message: str) -> None:
        """Сообщение о новом шаге обработки."""

    def update(self, message: str) -> None:
        """Обновление текущего шага (может перерисовываться в одной строке)."""


def find_srt_files(input_dir: Path) -> list[Path]:
    """Возвращает список .srt-файлов в папке."""
    if not input_dir.exists():
        raise ConverterError(f"Папка с субтитрами не найдена: {input_dir}")
    if not input_dir.is_dir():
        raise ConverterError(f"Путь не является папкой: {input_dir}")
    return [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and path.suffix.lower() == ".srt"
    ]


def split_text(text: str, max_chars: int) -> list[str]:
    """Делит текст на фрагменты не длиннее max_chars по границам предложений."""
    sentences = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        for piece in _split_long_sentence(sentence, max_chars):
            if not current:
                current = piece
            elif len(current) + 1 + len(piece) <= max_chars:
                current = f"{current} {piece}"
            else:
                chunks.append(current)
                current = piece

    if current:
        chunks.append(current)
    return chunks


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """Режет слишком длинное предложение по словам, если оно не влезает целиком."""
    parts: list[str] = []
    rest = sentence.strip()
    while len(rest) > max_chars:
        cut = rest.rfind(" ", 0, max_chars)
        if cut < max_chars // 2:
            cut = max_chars
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


def output_path_for(srt_path: Path, settings: Settings, name: str | None = None) -> Path:
    """Строит путь к mp3-файлу для исходных субтитров."""
    stem = name or srt_path.stem
    return settings.output_dir / f"{stem}{settings.paths.output_extension}"


def srt_to_mp3(
    srt_path: Path,
    output_path: Path,
    settings: Settings,
    reporter: Reporter,
) -> Path:
    """Озвучивает субтитры и сохраняет mp3. Возвращает путь к файлу."""
    if output_path.exists() and not settings.processing.overwrite:
        raise ConverterError(
            f"Файл уже существует: {output_path}\n"
            "Удалите его или запустите с ключом --overwrite."
        )

    reporter.stage(f"Читаю субтитры: {srt_path.name}")
    cues = parse_srt(read_srt(srt_path))
    text = cues_to_text(cues, settings.processing.segment_joiner)
    if not text:
        raise ConverterError(f"В файле {srt_path.name} нет текста для озвучки.")

    chunks = split_text(text, settings.processing.chunk_max_chars)
    voice = settings.voice
    reporter.stage(
        f"Реплик: {len(cues)}, фрагментов озвучки: {len(chunks)}, "
        f"голос: {voice.name}"
    )

    audio = asyncio.run(
        _synthesize_chunks(chunks, voice, reporter, settings.processing.retries)
    )
    audio = _match_timeline(audio, chunks, cues, settings, reporter)

    reporter.stage(f"Записываю {output_path.name} ({len(audio) / 1024:.0f} КБ)")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
    except OSError as exc:
        raise ConverterError(f"Не удалось записать {output_path}: {exc}") from exc

    return output_path


def _match_timeline(
    audio: bytes,
    chunks: Sequence[str],
    cues: Sequence[Cue],
    settings: Settings,
    reporter: Reporter,
) -> bytes:
    """Подгоняет длительность озвучки под длительность видео (таймкод).

    Ориентир — время окончания последней реплики субтитров. Темп чтения
    синтезатора не совпадает с темпом исходной речи: в плотных субтитрах
    голос не успевает (mp3 длиннее), в разреженных — наоборот, заканчивает
    раньше. Поэтому длительность измеряется по mp3-фреймам и, если она
    расходится с ориентиром больше чем на 2 %, речь переозвучивается с
    другим темпом — в сторону ускорения или замедления.
    """
    timeline = timeline_seconds(cues)
    duration = duration_seconds(audio)
    if timeline <= 0 or duration <= 0:
        return audio

    voice = settings.voice
    if not settings.processing.fit_timeline:
        _report_timeline(reporter, duration, timeline)
        return audio

    try:
        rate = voice.rate_percent
    except ValueError:
        reporter.stage(
            f"Темп {voice.rate!r} задан не в процентах — подгонку под таймкод пропускаю"
        )
        _report_timeline(reporter, duration, timeline)
        return audio

    limit = settings.processing.max_rate_percent
    for _ in range(_FIT_ATTEMPTS):
        if _fits_timeline(duration, timeline):
            break

        target = _required_rate_percent(rate, duration, timeline)
        target = max(_MIN_RATE_PERCENT, min(target, limit))
        if target == rate:
            reporter.stage(
                f"Озвучка расходится с таймкодом ({_format_time(duration)} против "
                f"{_format_time(timeline)}), темп уже на пределе {voice.rate}"
            )
            break

        reporter.stage(
            f"{'Речь длиннее' if duration > timeline else 'Речь короче'} таймкода "
            f"({_format_time(duration)} против {_format_time(timeline)}) — "
            f"переозвучиваю с темпом {format_rate_percent(target)}"
        )
        voice = voice.with_rate_percent(target)
        rate = target
        audio = asyncio.run(
            _synthesize_chunks(chunks, voice, reporter, settings.processing.retries)
        )
        duration = duration_seconds(audio)
        if duration <= 0:
            break

    _report_timeline(reporter, duration, timeline)
    return audio


def _fits_timeline(duration: float, timeline: float) -> bool:
    """Укладывается ли озвучка в таймкод с допустимым расхождением."""
    return abs(duration / timeline - 1) <= _FIT_TOLERANCE


def _required_rate_percent(rate_percent: int, duration: float, target: float) -> int:
    """Считает темп, при котором озвучка займёт нужное время.

    Длительность речи обратно пропорциональна темпу, поэтому пересчёт идёт
    через коэффициент (1 + rate/100): например, при длительности 383 с и
    ориентире 286 с нужен темп +34 %.
    """
    factor = 1 + rate_percent / 100
    return round((factor * duration / target - 1) * 100)


def _report_timeline(reporter: Reporter, duration: float, timeline: float) -> None:
    """Сообщает итоговую длительность и расхождение с таймкодом."""
    if duration <= 0 or timeline <= 0:
        return
    reporter.stage(
        f"Длительность: {_format_time(duration)}, "
        f"таймкод субтитров: {_format_time(timeline)} "
        f"({duration / timeline - 1:+.0%})"
    )


def _format_time(seconds: float) -> str:
    """Форматирует секунды как "6:23" или "1:02:03"."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


async def _synthesize_chunks(
    chunks: Sequence[str],
    voice: VoiceConfig,
    reporter: Reporter,
    retries: int = 0,
) -> bytes:
    """Озвучивает фрагменты по очереди и склеивает mp3-данные."""
    audio = bytearray()
    total = len(chunks)
    step_bytes = 32 * 1024

    for number, chunk in enumerate(chunks, start=1):
        reporter.stage(
            f"Озвучиваю фрагмент {number}/{total} ({len(chunk)} символов)"
        )
        reported = 0

        def on_bytes(size: int, number: int = number, total: int = total) -> None:
            nonlocal reported
            if size - reported < step_bytes:
                return
            reported = size
            reporter.update(f"Фрагмент {number}/{total}: {size / 1024:.0f} КБ")

        def on_retry(
            attempt: int,
            attempts: int,
            delay: float,
            error: TtsError,
            number: int = number,
            total: int = total,
        ) -> None:
            reporter.stage(
                f"Фрагмент {number}/{total}: {error}; "
                f"повтор {attempt}/{attempts} через {delay:.0f} с"
            )

        audio.extend(
            await synthesize(
                chunk,
                voice,
                on_bytes=on_bytes,
                retries=retries,
                on_retry=on_retry,
            )
        )
        reporter.update(f"Фрагмент {number}/{total}: готово, {len(chunk)} символов")

    return bytes(audio)
