"""Настройки приложения: значения по умолчанию и загрузка из config.json."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, TypeVar

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

T = TypeVar("T")


class ConfigError(Exception):
    """Файл настроек отсутствует или содержит некорректные значения."""


@dataclass
class PathsConfig:
    """Где искать субтитры и куда складывать результат."""

    input_dir: str = "input"
    output_dir: str = "output"
    output_extension: str = ".mp3"


@dataclass
class VoiceConfig:
    """Параметры синтеза речи edge-tts."""

    name: str = "ru-RU-DmitryNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    pitch: str = "+0Hz"

    @property
    def rate_percent(self) -> int:
        """Темп речи в процентах (например, 35 для "+35%")."""
        return parse_rate_percent(self.rate)

    def with_rate_percent(self, percent: int) -> "VoiceConfig":
        """Копия настроек голоса с другим темпом речи."""
        return replace(self, rate=format_rate_percent(percent))


@dataclass
class ProcessingConfig:
    """Как обрабатывать текст субтитров и результат."""

    chunk_max_chars: int = 3000
    overwrite: bool = False
    retries: int = 4
    request_concurrency: int = 3
    fit_timeline: bool = True
    max_rate_percent: int = 100
    block_max_seconds: float = 12.0
    block_align: str = "auto"
    block_gap_max_seconds: float = 2.0


# Куда ставить озвученный блок внутри его окна в таймкоде.
BLOCK_ALIGN_MODES = ("auto", "start", "end")


@dataclass
class Settings:
    """Полный набор настроек приложения."""

    paths: PathsConfig
    voice: VoiceConfig
    processing: ProcessingConfig
    source: Path

    @property
    def input_dir(self) -> Path:
        """Папка с исходными субтитрами (пути считаются от файла настроек)."""
        return _resolve(self.paths.input_dir, self.source.parent)

    @property
    def output_dir(self) -> Path:
        """Папка для готовых mp3-файлов."""
        return _resolve(self.paths.output_dir, self.source.parent)


def _resolve(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


_RATE_RE = re.compile(r"^([+-]?)(\d{1,3})\s*%$")
# Форматы, которые принимает edge-tts: знак обязателен ("10%" сервис отвергнет).
_VOICE_PERCENT_RE = re.compile(r"^[+-]\d+%$")
_VOICE_PITCH_RE = re.compile(r"^[+-]\d+Hz$")


def parse_rate_percent(rate: str) -> int:
    """Разбирает темп edge-tts ("+10%") в целое число процентов (10)."""
    match = _RATE_RE.match(rate.strip())
    if not match:
        raise ValueError(
            f"Темп должен быть в формате \"+10%\" или \"-20%\", получено: {rate!r}"
        )
    sign, value = match.groups()
    return -int(value) if sign == "-" else int(value)


def format_rate_percent(percent: int) -> str:
    """Собирает темп edge-tts из числа процентов: 35 -> "+35%"."""
    return f"{percent:+d}%"


def _load_section(cls: type[T], raw: dict[str, Any], name: str) -> T:
    section = raw.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"Секция \"{name}\" в config.json должна быть объектом.")
    known = {field.name for field in fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(section) - known)
    if unknown:
        raise ConfigError(
            f"Неизвестные параметры в секции \"{name}\": {', '.join(unknown)}."
        )
    try:
        return cls(**section)  # type: ignore[call-arg]
    except TypeError as exc:
        raise ConfigError(f"Ошибка в секции \"{name}\": {exc}") from exc


def _require_str(value: Any, name: str) -> str:
    """Проверяет, что параметр — непустая строка.

    JSON допускает числа и ``null`` там, где ожидается строка; без такой
    проверки ошибка выглядела бы как трейсбек ``AttributeError`` вместо
    понятного сообщения о настройках.
    """
    if not isinstance(value, str):
        raise ConfigError(f"Параметр {name} должен быть строкой, получено: {value!r}.")
    if not value.strip():
        raise ConfigError(f"Параметр {name} не может быть пустым.")
    return value


def _validate(settings: Settings) -> None:
    voice = settings.voice
    processing = settings.processing

    _require_str(settings.paths.input_dir, "paths.input_dir")
    _require_str(settings.paths.output_dir, "paths.output_dir")
    _require_str(settings.paths.output_extension, "paths.output_extension")
    _require_str(voice.name, "voice.name")
    _require_str(voice.rate, "voice.rate")
    _require_str(voice.volume, "voice.volume")
    _require_str(voice.pitch, "voice.pitch")
    _require_str(processing.block_align, "processing.block_align")

    # Форматы проверяем сразу: edge-tts отвергает "10%" без знака, и без этой
    # проверки запрос падал бы уже на синтезе, после нескольких повторов.
    for name, value, pattern, example in (
        ("voice.rate", voice.rate, _VOICE_PERCENT_RE, '"+10%"'),
        ("voice.volume", voice.volume, _VOICE_PERCENT_RE, '"+0%"'),
        ("voice.pitch", voice.pitch, _VOICE_PITCH_RE, '"+0Hz"'),
    ):
        if not pattern.match(value.strip()):
            raise ConfigError(
                f"Параметр {name} должен быть в формате {example}, получено: {value!r}."
            )

    if (
        isinstance(processing.chunk_max_chars, bool)
        or not isinstance(processing.chunk_max_chars, int)
        or processing.chunk_max_chars < 200
    ):
        raise ConfigError(
            "Параметр processing.chunk_max_chars должен быть целым числом не меньше 200."
        )
    extension = settings.paths.output_extension
    if not extension.startswith(".") or len(extension) < 2:
        raise ConfigError(
            "Параметр paths.output_extension должен начинаться с точки, например \".mp3\"."
        )
    if (
        isinstance(processing.retries, bool)
        or not isinstance(processing.retries, int)
        or processing.retries < 0
    ):
        raise ConfigError("Параметр processing.retries должен быть целым числом не меньше 0.")
    if (
        isinstance(processing.request_concurrency, bool)
        or not isinstance(processing.request_concurrency, int)
        or not 1 <= processing.request_concurrency <= 16
    ):
        raise ConfigError(
            "Параметр processing.request_concurrency должен быть целым числом от 1 до 16."
        )
    max_rate = processing.max_rate_percent
    if isinstance(max_rate, bool) or not isinstance(max_rate, int) or not 0 <= max_rate <= 100:
        raise ConfigError(
            "Параметр processing.max_rate_percent должен быть целым числом от 0 до 100."
        )
    if not isinstance(processing.fit_timeline, bool):
        raise ConfigError("Параметр processing.fit_timeline должен быть true или false.")
    block_seconds = processing.block_max_seconds
    if isinstance(block_seconds, bool) or not isinstance(block_seconds, (int, float)) or (
        block_seconds < 0
    ):
        raise ConfigError(
            "Параметр processing.block_max_seconds должен быть числом не меньше 0 "
            "(0 — озвучивать каждую реплику отдельно)."
        )
    if processing.block_align not in BLOCK_ALIGN_MODES:
        raise ConfigError(
            "Параметр processing.block_align должен быть одним из: "
            f"{', '.join(BLOCK_ALIGN_MODES)}."
        )
    block_gap = processing.block_gap_max_seconds
    if isinstance(block_gap, bool) or not isinstance(block_gap, (int, float)) or (
        block_gap < 0
    ):
        raise ConfigError(
            "Параметр processing.block_gap_max_seconds должен быть числом не меньше 0 "
            "(пауза в субтитрах, которая ещё остаётся внутри блока)."
        )


def validate_settings(settings: Settings) -> None:
    """Проверяет настройки после загрузки и правок из командной строки."""
    _validate(settings)


def load_settings(config_path: str | Path | None = None) -> Settings:
    """Читает настройки из JSON, подставляя значения по умолчанию."""
    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    if config_path is not None and not path.is_file():
        raise ConfigError(f"Файл настроек не найден: {path}")

    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Файл {path} содержит некорректный JSON: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Не удалось прочитать {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Корень config.json должен быть объектом.")

    settings = Settings(
        paths=_load_section(PathsConfig, raw, "paths"),
        voice=_load_section(VoiceConfig, raw, "voice"),
        processing=_load_section(ProcessingConfig, raw, "processing"),
        source=path,
    )
    try:
        _validate(settings)
    except ConfigError:
        raise
    except (TypeError, AttributeError, ValueError) as exc:
        # Страховка: неожиданный тип в JSON не должен выглядеть как трейсбек.
        raise ConfigError(f"Ошибка в настройках {path}: {exc}") from exc
    return settings
