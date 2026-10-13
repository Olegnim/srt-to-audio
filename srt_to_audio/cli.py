"""Командный интерфейс: разбор аргументов, вывод прогресса и ошибок."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, Settings, load_settings
from .converter import ConverterError, find_srt_files, output_path_for, srt_to_mp3
from .srt_parser import SrtError
from .tts import TtsError, list_voices

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130


class ConsoleReporter:
    """Печатает шаги обработки, обновляя текущий прогресс в одной строке."""

    _WIDTH = 70

    def __init__(self) -> None:
        stream = sys.stdout
        isatty = getattr(stream, "isatty", None)
        self._interactive = bool(isatty and isatty())
        self._live = False

    def stage(self, message: str) -> None:
        self._clear()
        print(f"  - {message}")

    def update(self, message: str) -> None:
        # В файл или пайп прогресс не перерисовываем, чтобы не засорять вывод.
        if not self._interactive:
            return
        print(f"\r  ... {message:<{self._WIDTH}}", end="", flush=True)
        self._live = True

    def finish(self) -> None:
        self._clear()

    def _clear(self) -> None:
        if self._live:
            print("\r" + " " * (self._WIDTH + 8) + "\r", end="", flush=True)
            self._live = False


def _setup_console() -> None:
    """Переводит вывод в UTF-8: на Windows иначе возможен UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="srt-to-audio",
        description="Озвучивает SRT-субтитры из папки input и сохраняет mp3 в папку output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python main.py                       обработать все .srt из папки input\n"
            "  python main.py -f movie.srt          только один файл\n"
            "  python main.py -f movie.srt -o voice переименовать результат\n"
            "  python main.py --voice ru-RU-SvetlanaNeural --rate \"+10%\"\n"
            "  python main.py --list-voices ru      посмотреть доступные голоса\n"
        ),
    )
    parser.add_argument(
        "-f",
        "--file",
        metavar="ФАЙЛ",
        help="имя файла из папки input (по умолчанию обрабатываются все .srt)",
    )
    parser.add_argument(
        "-o",
        "--out",
        metavar="ИМЯ",
        help="имя выходного файла без расширения (только для одного входного файла)",
    )
    parser.add_argument("--voice", metavar="ГОЛОС", help="голос edge-tts")
    parser.add_argument("--rate", metavar="ТЕМП", help='темп речи, например "+10%%" или "-20%%"')
    parser.add_argument("--volume", metavar="ГРОМКОСТЬ", help='громкость, например "+0%%"')
    parser.add_argument("--pitch", metavar="ТОН", help='тон, например "+0Hz" или "-20Hz"')
    parser.add_argument("--config", metavar="ПУТЬ", help="путь к файлу настроек (по умолчанию config.json)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=None,
        help="перезаписывать существующие mp3",
    )
    parser.add_argument(
        "--list-voices",
        nargs="?",
        const="",
        metavar="ЯЗЫК",
        help="показать доступные голоса (необязательно префикс языка, например ru)",
    )
    parser.add_argument("--version", action="version", version=f"srt-to-audio {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)

    try:
        settings = load_settings(args.config)
        _apply_overrides(settings, args)
    except ConfigError as exc:
        return _fail(str(exc))

    if args.list_voices is not None:
        return _print_voices(args.list_voices)

    try:
        files = _select_files(args.file, settings)
    except ConverterError as exc:
        return _fail(str(exc))

    if not files:
        return _fail(
            f"В папке {settings.input_dir} нет файлов .srt.\n"
            "Положите субтитры в папку input и запустите программу снова."
        )

    if args.out and len(files) > 1:
        return _fail("Ключ --out можно использовать только с одним входным файлом.")

    print(f"Голос: {settings.voice.name}  (темп {settings.voice.rate})")
    print(f"Вход:  {settings.input_dir}")
    print(f"Выход: {settings.output_dir}")

    failures = 0
    try:
        for position, srt_path in enumerate(files, start=1):
            print(f"\n[{position}/{len(files)}] {srt_path.name}")
            reporter = ConsoleReporter()
            try:
                target = srt_to_mp3(
                    srt_path,
                    output_path_for(srt_path, settings, args.out),
                    settings,
                    reporter,
                )
            except (ConverterError, SrtError, TtsError) as exc:
                reporter.finish()
                print(f"  ОШИБКА: {exc}", file=sys.stderr)
                failures += 1
                continue
            reporter.finish()
            print(f"  Готово: {target}  ({target.stat().st_size / 1024:.0f} КБ)")
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        return EXIT_INTERRUPTED

    print()
    if failures:
        print(f"Завершено с ошибками: {failures} из {len(files)}.")
        return EXIT_FAILED
    print(f"Готово: {len(files)} файл(ов) озвучено.")
    return EXIT_OK


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> None:
    """Переносит значения из командной строки в настройки."""
    if args.voice:
        settings.voice.name = args.voice
    if args.rate:
        settings.voice.rate = args.rate
    if args.volume:
        settings.voice.volume = args.volume
    if args.pitch:
        settings.voice.pitch = args.pitch
    if args.overwrite is not None:
        settings.processing.overwrite = args.overwrite


def _select_files(name: str | None, settings: Settings) -> list[Path]:
    if not name:
        return find_srt_files(settings.input_dir)

    candidate = Path(name).expanduser()
    if candidate.is_file():
        return [candidate]
    candidate = settings.input_dir / name
    if candidate.is_file():
        return [candidate]
    raise ConverterError(
        f"Файл не найден: {name}\n"
        f"Укажите имя файла из папки {settings.input_dir} или полный путь."
    )


def _print_voices(language: str | None) -> int:
    try:
        voices = asyncio.run(list_voices(language))
    except TtsError as exc:
        return _fail(str(exc))

    if not voices:
        return _fail("Подходящих голосов не найдено.")

    print(f"Доступно голосов: {len(voices)}")
    for name, details in voices:
        print(f"  {name:<40} {details}")
    return EXIT_OK


def _fail(message: str) -> int:
    print(f"ОШИБКА: {message}", file=sys.stderr)
    return EXIT_USAGE
