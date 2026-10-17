"""Длительность mp3-данных без внешних зависимостей.

Аудио от edge-tts приходит как поток mp3-фреймов MPEG (24 кГц, 48 кбит/с,
моно), поэтому длительность считается прямым подсчётом фреймов — ffmpeg,
mutagen и подобные библиотеки не нужны.
"""

from __future__ import annotations

# Таблицы битрейтов для Layer III (кбит/с) по индексу из заголовка фрейма.
_BITRATES_MPEG1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_BITRATES_MPEG2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_SAMPLE_RATES = {
    3: (44100, 48000, 32000),  # MPEG 1
    2: (22050, 24000, 16000),  # MPEG 2
    0: (11025, 12000, 8000),   # MPEG 2.5
}
_VERSION_BITS = {3, 2, 0}
_LAYER_III_BITS = 1


def _frame_info(header: bytes) -> tuple[float, int] | None:
    """Длительность одного фрейма (с) и его размер в байтах по заголовку."""
    if len(header) < 4 or header[0] != 0xFF or (header[1] & 0xE0) != 0xE0:
        return None

    version = (header[1] >> 3) & 0x03
    layer = (header[1] >> 1) & 0x03
    bitrate_index = (header[2] >> 4) & 0x0F
    sample_rate_index = (header[2] >> 2) & 0x03
    padding = (header[2] >> 1) & 0x01

    if (
        version not in _VERSION_BITS
        or layer != _LAYER_III_BITS
        or bitrate_index in (0, 15)
        or sample_rate_index == 3
    ):
        return None

    table = _BITRATES_MPEG1 if version == 3 else _BITRATES_MPEG2
    bitrate = table[bitrate_index] * 1000
    sample_rate = _SAMPLE_RATES[version][sample_rate_index]
    samples_per_frame = 1152 if version == 3 else 576

    seconds = samples_per_frame / sample_rate
    size = int(samples_per_frame / 8 * bitrate / sample_rate) + padding
    if size <= 1:
        return None
    return seconds, size


def duration_seconds(data: bytes) -> float:
    """Длительность mp3-данных в секундах (0.0, если фреймы не найдены)."""
    position = 0
    total = 0.0
    size = len(data)

    # Пропускаем ID3v2-тег в начале файла, если он есть.
    if data[:3] == b"ID3" and size > 10:
        tag_size = (
            (data[6] & 0x7F) << 21
            | (data[7] & 0x7F) << 14
            | (data[8] & 0x7F) << 7
            | (data[9] & 0x7F)
        )
        position = 10 + tag_size

    while position + 4 <= size:
        info = _frame_info(data[position:position + 4])
        if info is None:
            position += 1
            continue
        seconds, frame_size = info
        total += seconds
        position += frame_size

    return total
