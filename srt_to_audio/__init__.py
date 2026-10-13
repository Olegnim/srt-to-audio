"""Озвучка SRT-субтитров в mp3 через edge-tts."""

from .converter import ConverterError, srt_to_mp3
from .srt_parser import Cue, SrtError, parse_srt
from .tts import TtsError

__all__ = [
    "Cue",
    "ConverterError",
    "SrtError",
    "TtsError",
    "parse_srt",
    "srt_to_mp3",
]

__version__ = "1.0.0"
