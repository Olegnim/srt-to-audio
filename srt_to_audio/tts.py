"""Синтез речи через edge-tts."""

from __future__ import annotations

import asyncio
from typing import Callable

import edge_tts

from .config import VoiceConfig


class TtsError(Exception):
    """Ошибка обращения к сервису синтеза речи."""


async def synthesize(
    text: str,
    voice: VoiceConfig,
    on_bytes: Callable[[int], None] | None = None,
    retries: int = 0,
) -> bytes:
    """Озвучивает текст и возвращает mp3-данные, повторяя запрос при сбоях."""
    if not text.strip():
        raise TtsError("Пустой текст для озвучки.")

    for attempt in range(retries + 1):
        try:
            return await _synthesize_once(text, voice, on_bytes)
        except TtsError as exc:
            if attempt >= retries:
                raise TtsError(f"{exc} (попыток: {retries + 1})") from exc
            await asyncio.sleep(1.5 * (attempt + 1))

    raise TtsError("Не удалось синтезировать речь.")


async def _synthesize_once(
    text: str,
    voice: VoiceConfig,
    on_bytes: Callable[[int], None] | None,
) -> bytes:
    communicate = edge_tts.Communicate(
        text,
        voice.name,
        rate=voice.rate,
        volume=voice.volume,
        pitch=voice.pitch,
    )

    audio = bytearray()
    try:
        async for chunk in communicate.stream():
            if chunk["type"] != "audio":
                continue
            audio.extend(chunk["data"])
            if on_bytes is not None:
                on_bytes(len(audio))
    except edge_tts.exceptions.NoAudioReceived as exc:
        raise TtsError(
            "Сервис не вернул аудио: проверьте голос и параметры синтеза."
        ) from exc
    except Exception as exc:  # сетевые и протокольные сбои edge-tts
        raise TtsError(f"Не удалось получить аудио от edge-tts: {exc}") from exc

    if not audio:
        raise TtsError("Сервис edge-tts вернул пустой ответ.")
    return bytes(audio)


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
