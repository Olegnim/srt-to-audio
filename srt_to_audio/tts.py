"""Синтез речи через edge-tts."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Callable

import edge_tts

from .config import VoiceConfig

# Неофициальный сервис edge-tts периодически отвечает 403 или закрывает
# соединение без аудио. Это временные сбои, поэтому пауза между попытками
# растёт: частые повторы подряд лишь продлевают блокировку.
_RETRY_BASE_SECONDS = 2.0
_RETRY_MAX_SECONDS = 15.0
_RETRY_JITTER = 0.25

# Отметки слов приходят в единицах по 100 нс.
_TICKS_PER_SECOND = 10_000_000

_NO_AUDIO_MESSAGE = (
    "Сервис edge-tts не вернул аудио (временный сбой или ограничение частоты запросов)"
)


class TtsError(Exception):
    """Ошибка обращения к сервису синтеза речи."""


@dataclass(frozen=True)
class Speech:
    """Озвученный текст и границы самой речи внутри mp3-данных.

    edge-tts добавляет к реплике служебную тишину (до ~0,25 с в начале и около
    секунды в конце), из-за которой озвучка «не попадает» в таймкод. Границы
    берутся из отметок слов, которые сервис присылает вместе с аудио.
    ``words`` — время начала каждого слова от начала потока: по нему можно
    восстановить паузы внутри слитно прочитанного блока.
    """

    audio: bytes
    start: float
    end: float
    words: tuple[float, ...] = ()


RetryCallback = Callable[[int, int, float, "TtsError"], None]


def _retry_delay(attempt: int) -> float:
    """Пауза перед повтором: 2, 4, 8, 15 секунд с небольшим случайным разбросом."""
    delay = min(_RETRY_BASE_SECONDS * 2**attempt, _RETRY_MAX_SECONDS)
    return delay * (1 + _RETRY_JITTER * random.random())


async def synthesize(
    text: str,
    voice: VoiceConfig,
    on_bytes: Callable[[int], None] | None = None,
    retries: int = 0,
    on_retry: RetryCallback | None = None,
) -> Speech:
    """Озвучивает текст и возвращает mp3-данные с границами речи.

    ``on_retry`` вызывается перед каждой повторной попыткой и получает номер
    следующей попытки, их общее количество, паузу в секундах и ошибку.
    """
    if not text.strip():
        raise TtsError("Пустой текст для озвучки.")

    total = retries + 1
    for attempt in range(total):
        try:
            return await _synthesize_once(text, voice, on_bytes)
        except TtsError as exc:
            if attempt >= retries:
                raise TtsError(f"{exc} (попыток: {total})") from exc
            delay = _retry_delay(attempt)
            if on_retry is not None:
                on_retry(attempt + 2, total, delay, exc)
            await asyncio.sleep(delay)

    raise TtsError("Не удалось синтезировать речь.")


async def _synthesize_once(
    text: str,
    voice: VoiceConfig,
    on_bytes: Callable[[int], None] | None,
) -> Speech:
    communicate = edge_tts.Communicate(
        text,
        voice.name,
        rate=voice.rate,
        volume=voice.volume,
        pitch=voice.pitch,
        boundary="WordBoundary",
    )

    audio = bytearray()
    speech_start: float | None = None
    speech_end = 0.0
    words: list[float] = []
    try:
        async for chunk in communicate.stream():
            kind = chunk["type"]
            if kind == "audio":
                audio.extend(chunk["data"])
                if on_bytes is not None:
                    on_bytes(len(audio))
            elif kind == "WordBoundary":
                offset = chunk["offset"] / _TICKS_PER_SECOND
                end = offset + chunk["duration"] / _TICKS_PER_SECOND
                words.append(offset)
                speech_start = offset if speech_start is None else min(speech_start, offset)
                speech_end = max(speech_end, end)
    except edge_tts.exceptions.NoAudioReceived as exc:
        raise TtsError(_NO_AUDIO_MESSAGE) from exc
    except Exception as exc:  # сетевые и протокольные сбои edge-tts
        raise TtsError(f"Не удалось получить аудио от edge-tts: {exc}") from exc

    if not audio:
        raise TtsError("Сервис edge-tts вернул пустой ответ.")
    return Speech(
        audio=bytes(audio),
        start=speech_start or 0.0,
        end=speech_end,
        words=tuple(words),
    )


async def list_voices(language_prefix: str | None = None) -> list[tuple[str, str]]:
    """Возвращает список доступных голосов: (имя, описание)."""
    try:
        voices = await edge_tts.list_voices()
    except Exception as exc:
        raise TtsError(f"Не удалось получить список голосов: {exc}") from exc

    prefix = (language_prefix or "").lower()
    result = [
        (
            str(voice.get("ShortName", "")),
            f"{voice.get('Gender', '?')}, {voice.get('Locale', '?')}",
        )
        for voice in voices
        if prefix in str(voice.get("Locale", "")).lower()
    ]
    return sorted(result)
