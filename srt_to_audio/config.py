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


def _validate(settings: Settings) -> None:
    if not settings.voice.name.strip():
        raise ConfigError("Параметр voice.name не может быть пустым.")
    if not isinstance(settings.processing.chunk_max_chars, int) or (
        settings.processing.chunk_max_chars < 200
    ):
        raise ConfigError(
            "Параметр processing.chunk_max_chars должен быть целым числом не меньше 200."
        )
    extension = settings.paths.output_extension
    if not extension.startswith(".") or len(extension) < 2:
        raise ConfigError(
            "Параметр paths.output_extension должен начинаться с точки, например \".mp3\"."
        )
    if not isinstance(settings.processing.retries, int) or settings.processing.retries < 0:
        raise ConfigError("Параметр processing.retries должен быть целым числом не меньше 0.")
    max_rate = settings.processing.max_rate_percent
    if not isinstance(max_rate, int) or not 0 <= max_rate <= 100:
        raise ConfigError(
            "Параметр processing.max_rate_percent должен быть целым числом от 0 до 100."
        )
    if not isinstance(settings.processing.fit_timeline, bool):
        raise ConfigError("Параметр processing.fit_timeline должен быть true или false.")
    block_seconds = settings.processing.block_max_seconds
    if isinstance(block_seconds, bool) or not isinstance(block_seconds, (int, float)) or (
        block_seconds < 0
    ):
        raise ConfigError(
            "Параметр processing.block_max_seconds должен быть числом не меньше 0 "
            "(0 — озвучивать каждую реплику отдельно)."
        )
    if settings.processing.block_align not in BLOCK_ALIGN_MODES:
        raise ConfigError(
            "Параметр processing.block_align должен быть одним из: "
            f"{', '.join(BLOCK_ALIGN_MODES)}."
        )
    block_gap = settings.processing.block_gap_max_seconds
    if isinstance(block_gap, bool) or not isinstance(block_gap, (int, float)) or (
        block_gap < 0
    ):
        raise ConfigError(
            "Параметр processing.block_gap_max_seconds должен быть числом не меньше 0 "
            "(пауза в субтитрах, которая ещё остаётся внутри блока)."
        )


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
    _validate(settings)
    return settings
