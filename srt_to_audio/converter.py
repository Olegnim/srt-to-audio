"""Преобразование SRT-файла в mp3-дорожку."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, Sequence

from .config import Settings, VoiceConfig, format_rate_percent
from .mp3 import duration_seconds, frame_duration, insert_silence, silence_like, trim
from .srt_parser import Cue, parse_srt, read_srt, timeline_seconds, timestamp_seconds
from .tts import TtsError, synthesize

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])[\s\n]+")

# Реплика считается уложившейся в свой блок, если расхождение не больше 2 %:
# это доли секунды, на слух незаметно и не сдвигает следующую реплику.
_FIT_TOLERANCE = 0.02
# Одного переозвучивания обычно хватает; второе — на случай, если длительность
# оказалась не строго обратно пропорциональна темпу.
_FIT_ATTEMPTS = 2
# Сколько оставить после последнего слова, чтобы не срезать окончание.
_TRIM_TAIL_SECONDS = 0.1
# Пауза между репликами, когда привязка к таймкоду отключена (--no-fit).
_NO_FIT_PAUSE_SECONDS = 0.25
# Сколько запросов к edge-tts отправлять одновременно: больше — быстрее, но
# сервис начинает отвечать 403 и запросы уходят в повторы.
_REQUEST_CONCURRENCY = 3
# Сдвиг реплики вперёд меньше этого значения считаем нормой.
_SHIFT_TOLERANCE = 0.05
# Пауза в субтитрах длиннее этого значения по умолчанию режет укрупнённый блок
# (значение переопределяется в config.json: processing.block_gap_max_seconds).
_DEFAULT_BLOCK_GAP_MAX_SECONDS = 2.0
# Паузы короче этого значения — погрешность таймкодов субтитров, а не речь:
# внутри блока их не восстанавливаем.
_MIN_BLOCK_PAUSE_SECONDS = 0.15
# Сколько тишины можно добавить к паузе внутри блока, чтобы догнать таймкод:
# остаток окна сверх этого остаётся в конце блока, а не растягивает паузу.
_MAX_EXTRA_PAUSE_SECONDS = 0.75


@dataclass(frozen=True)
class FragmentPause:
    """Пауза внутри блока озвучки.

    ``before_word`` — номер слова блока, перед которым в субтитрах была пауза,
    ``seconds`` — её длительность. Синтезатор читает блок слитно, и без такой
    вставки следующие реплики блока звучали бы раньше своих таймкодов.
    """

    before_word: int
    seconds: float


@dataclass(frozen=True)
class Fragment:
    """Фрагмент озвучки (укрупнённый блок реплик) и его окно в таймкоде."""

    text: str
    start: float
    end: float
    pauses: tuple[FragmentPause, ...] = ()

    @property
    def window(self) -> float:
        """Сколько времени отведено на фрагмент по субтитрам."""
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class Clip:
    """Озвученный фрагмент: mp3-данные и время начала слов от их начала."""

    audio: bytes
    words: tuple[float, ...] = ()

    @property
    def duration(self) -> float:
        """Длительность озвучки в секундах."""
        return duration_seconds(self.audio)


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def count_words(text: str) -> int:
    """Считает слова в тексте так же, как их нумерует синтезатор."""
    return len(_WORD_RE.findall(text))


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


def build_fragments(
    cues: Sequence[Cue],
    max_chars: int,
    block_max_seconds: float = 0.0,
    gap_max_seconds: float = _DEFAULT_BLOCK_GAP_MAX_SECONDS,
) -> list[Fragment]:
    """Готовит фрагменты озвучки — блоки из соседних реплик.

    Блок укрупняется до ``block_max_seconds`` секунд (в config.json по умолчанию
    12 с), чтобы речь звучала связно, как синхронный перевод: синтезатор читает
    несколько реплик подряд одной фразой, не разрываясь на каждой строке
    субтитров. Значение 0 отключает укрупнение — блоком считается одна реплика.

    Пауза в исходных субтитрах длиннее ``gap_max_seconds`` режет блок: иначе
    текст «перепрыгнул» бы через молчание в кадре. Паузы покороче остаются
    внутри блока, но запоминаются (``Fragment.pauses``), чтобы при сборке
    дорожки вернуть на их место тишину: без этого реплики, идущие после паузы,
    звучали бы раньше своих таймкодов. Слишком длинный блок (длиннее
    ``max_chars``) режется на части, а его окно делится между ними
    пропорционально объёму текста.
    """
    blocks: list[Fragment] = []
    texts: list[str] = []
    pauses: list[FragmentPause] = []
    words = 0
    block_start = 0.0
    block_end = 0.0
    previous_end = 0.0

    def flush() -> None:
        nonlocal texts, pauses, words
        if texts:
            blocks.append(
                Fragment(
                    text=" ".join(texts),
                    start=block_start,
                    end=block_end,
                    pauses=tuple(pauses),
                )
            )
            texts = []
            pauses = []
            words = 0

    for cue in cues:
        text = cue.text.strip()
        if not text:
            continue
        try:
            start = timestamp_seconds(cue.start)
            end = timestamp_seconds(cue.end)
        except ValueError:
            # Битая метка: реплику озвучим, но выравнивать по времени не будем.
            start = end = previous_end
        if end < start:
            end = start
        previous_end = max(previous_end, end)

        cue_words = count_words(text)
        if texts:
            gap = start - block_end
            too_long = end - block_start > block_max_seconds
            if block_max_seconds <= 0 or too_long or gap > gap_max_seconds:
                flush()
            elif gap >= _MIN_BLOCK_PAUSE_SECONDS and cue_words:
                # Пауза остаётся внутри блока — запомним её место по номеру слова.
                pauses.append(FragmentPause(before_word=words, seconds=gap))

        if not texts:
            block_start = start
        block_end = end
        texts.append(text)
        words += cue_words

    flush()

    fragments: list[Fragment] = []
    for block in blocks:
        fragments.extend(_split_block(block, max_chars))
    return fragments


def _split_block(fragment: Fragment, max_chars: int) -> list[Fragment]:
    """Делит слишком длинный блок на части и раздаёт им окно по объёму текста."""
    if max_chars <= 0 or len(fragment.text) <= max_chars:
        return [fragment]

    parts = split_text(fragment.text, max_chars)
    if len(parts) <= 1:
        return [fragment]

    total = sum(len(part) for part in parts)
    window = fragment.end - fragment.start
    fragments: list[Fragment] = []
    cursor = fragment.start
    first_word = 0
    for part in parts:
        share = window * len(part) / total
        part_words = count_words(part)
        pauses = tuple(
            FragmentPause(before_word=pause.before_word - first_word, seconds=pause.seconds)
            for pause in fragment.pauses
            if first_word < pause.before_word < first_word + part_words
        )
        fragments.append(
            Fragment(text=part, start=cursor, end=cursor + share, pauses=pauses)
        )
        cursor += share
        first_word += part_words
    return fragments


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
    processing = settings.processing
    fragments = build_fragments(
        cues,
        processing.chunk_max_chars,
        processing.block_max_seconds,
        processing.block_gap_max_seconds,
    )
    if not fragments:
        raise ConverterError(f"В файле {srt_path.name} нет текста для озвучки.")

    reporter.stage(
        f"Реплик: {len(cues)}, блоков озвучки: {len(fragments)} "
        f"(до {processing.block_max_seconds:g} с), голос: {settings.voice.name}"
    )
    audio = asyncio.run(_render(fragments, timeline_seconds(cues), settings, reporter))

    reporter.stage(f"Записываю {output_path.name} ({len(audio) / 1024:.0f} КБ)")
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
    except OSError as exc:
        raise ConverterError(f"Не удалось записать {output_path}: {exc}") from exc

    return output_path


async def _render(
    fragments: Sequence[Fragment],
    timeline: float,
    settings: Settings,
    reporter: Reporter,
) -> bytes:
    """Озвучивает фрагменты и раскладывает их по таймкоду субтитров.

    Каждый фрагмент ставится в своё окно по таймкоду, а недостающее до него
    время заполняется тишиной. Темп правится только у тех блоков, где это нужно:
    речь длиннее окна — ускоряем (иначе блок сдвинет всё после себя); блок,
    который короче окна, читается в обычном темпе, а остаток окна — пауза.
    Остальные блоки читаются в темпе ``voice.rate``.

    Внутри окна фрагмент размещается как у синхронного переводчика
    (``processing.block_align``):

    - ``auto`` — обычный темп: речь идёт с начала блока и занимает его целиком
      до своего конца, остаток окна — пауза; если блок пришлось ускорять, речь
      прижимается к концу окна, чтобы закончить вместе с оригиналом;
    - ``start`` — всегда с начала блока;
    - ``end`` — всегда прижимать к концу блока.

    Паузы между репликами внутри блока восстанавливаются тишиной
    (``_apply_pauses``): блок читается слитно, и без этого его вторая-третья
    реплики звучали бы раньше своих таймкодов. Пусть лучше реплика прозвучит
    чуть позже картинки, чем раньше неё.
    """
    align = settings.processing.fit_timeline
    base_rate = _base_rate_percent(settings.voice, reporter)
    mode = settings.processing.block_align
    total = len(fragments)

    clips = await _synthesize_all(fragments, settings, reporter)
    if align:
        # В режиме auto остаток окна тоже раздаётся паузам внутри блока —
        # так каждая следующая реплика держится рядом со своим таймкодом.
        clips = _with_pauses(fragments, clips, fill_window=mode == "auto")
    rates: list[int | None] = [None] * total
    if align and base_rate is not None:
        clips, rates = await _fit_blocks(
            fragments, clips, settings, base_rate, reporter
        )

    audio = bytearray()
    position = 0.0
    reference: bytes | None = None
    overflow = 0
    shifted = 0

    for fragment, clip in zip(fragments, clips):
        data = clip.audio
        duration = clip.duration
        if align and fragment.window > 0:
            fits = duration <= _allowed_duration(fragment.window)
            start_at = _placement_start(mode, fragment, duration, fits, position)
            if not fits:
                overflow += 1
            if start_at - fragment.start > _SHIFT_TOLERANCE:
                shifted += 1
            audio.extend(silence_like(data, start_at - position))
            position = start_at + duration
        else:
            audio.extend(silence_like(data, _NO_FIT_PAUSE_SECONDS))
            position += duration + _NO_FIT_PAUSE_SECONDS
        audio.extend(data)
        reference = data

    # Добиваем дорожку до конца последней реплики: mp3 совпадает по длине с видео.
    if align and reference is not None and timeline > position:
        audio.extend(silence_like(reference, timeline - position))
        position = timeline

    rendered = bytes(audio)
    _report_timeline(reporter, duration_seconds(rendered), timeline or position)
    if align:
        sped_up = sum(1 for rate in rates if rate is not None and rate > (base_rate or 0))
        _report_fit(
            reporter,
            total=total,
            sped_up=sped_up,
            overflow=overflow,
            shifted=shifted,
            pauses=sum(len(fragment.pauses) for fragment in fragments),
        )
    return rendered


def _with_pauses(
    fragments: Sequence[Fragment],
    clips: Sequence[Clip],
    fill_window: bool = False,
) -> list[Clip]:
    """Возвращает паузы субтитров внутрь каждого слитно прочитанного блока."""
    return [
        _apply_pauses(fragment, clip, fill_window)
        for fragment, clip in zip(fragments, clips)
    ]


def _apply_pauses(fragment: Fragment, clip: Clip, fill_window: bool = False) -> Clip:
    """Вставляет тишину в те места блока, где в субтитрах была пауза.

    Синтезатор читает блок одной фразой, поэтому паузы между репликами
    «съедаются» и следующая реплика звучит раньше своего таймкода. Тишина
    ставится перед первым словом следующей реплики (``FragmentPause``), так что
    речь внутри блока снова совпадает с картинкой. Если разметка слов не
    совпала с текстом, пауза пропускается — лучше слитная речь, чем тишина
    в случайном месте.

    Паузы занимают только свободное место в окне блока: если речь и без них
    заполняет окно (или не влезает), тишина не вставляется, а паузы, которые
    не помещаются целиком, ужимаются пропорционально. Так восстановление пауз
    не заставляет ускорять речь.

    При ``fill_window`` (режим ``auto``) остаток окна тоже раздаётся паузам:
    тогда не только блок целиком, но и каждая следующая реплика внутри него
    оказывается рядом со своим таймкодом, а не забегает вперёд. Добавка
    ограничена: лучше оставить тишину в конце блока, чем растягивать паузу.
    """
    if not fragment.pauses or not clip.words:
        return clip

    pauses = [
        pause
        for pause in fragment.pauses
        if 0 < pause.before_word < len(clip.words)
    ]
    if not pauses or fragment.window <= 0:
        return clip

    # Сколько времени осталось в окне блока после речи.
    room = fragment.window - clip.duration
    if room <= 0:
        return clip

    paused = sum(pause.seconds for pause in pauses)
    scale = min(1.0, room / paused)
    extra = 0.0
    if fill_window:
        extra = min((room - paused * scale) / len(pauses), _MAX_EXTRA_PAUSE_SECONDS)
        extra = max(0.0, extra)

    # Тишина вставляется целыми кадрами, поэтому округляем вниз: иначе
    # накопленное округление вытолкнуло бы блок за пределы окна.
    frame = frame_duration(clip.audio)

    def fit_frames(seconds: float) -> float:
        if frame <= 0:
            return seconds
        return math.floor(seconds / frame + 1e-9) * frame

    audio = clip.audio
    inserted = 0.0
    for pause in pauses:
        seconds = fit_frames(pause.seconds * scale + extra)
        audio = insert_silence(audio, clip.words[pause.before_word] + inserted, seconds)
        inserted += seconds
    return replace(clip, audio=audio)


async def _synthesize_all(
    fragments: Sequence[Fragment],
    settings: Settings,
    reporter: Reporter,
) -> list[Clip]:
    """Озвучивает все фрагменты в обычном темпе, по несколько запросов сразу."""
    total = len(fragments)
    clips: list[Clip] = []
    for start in range(0, total, _REQUEST_CONCURRENCY):
        batch = fragments[start:start + _REQUEST_CONCURRENCY]
        reporter.update(
            f"Озвучиваю фрагменты {start + 1}–{start + len(batch)} из {total}"
        )
        clips.extend(
            await asyncio.gather(
                *(
                    _synthesize_text(
                        fragment.text,
                        settings.voice,
                        start + offset + 1,
                        total,
                        settings.processing.retries,
                        reporter,
                    )
                    for offset, fragment in enumerate(batch)
                )
            )
        )
    return clips


async def _fit_blocks(
    fragments: Sequence[Fragment],
    clips: list[Clip],
    settings: Settings,
    base_rate: int,
    reporter: Reporter,
) -> tuple[list[Clip], list[int | None]]:
    """Подгоняет темп блоков под их окна в таймкоде.

    Блок, речь которого длиннее окна, ускоряется — иначе он не влезет и сдвинет
    всё после себя. Блок короче окна читается в обычном темпе, а остаток окна
    остаётся тишиной: замедление звучало бы неестественно.
    """
    processing = settings.processing
    total = len(fragments)
    rates: list[int | None] = [None] * total

    accelerated = [
        number
        for number, fragment in enumerate(fragments)
        if fragment.window > 0
        and clips[number].duration > _allowed_duration(fragment.window)
    ]

    if not accelerated:
        reporter.stage(f"Все блоки ({total}) уложились в своё время без правки темпа")
        return clips, rates
    reporter.stage(
        f"Не влезли в своё время: {len(accelerated)} блок(ов) из {total} — ускоряю их"
    )

    for number in accelerated:
        rate = await _retempo_block(
            number,
            fragments,
            clips,
            settings,
            base_rate,
            (base_rate, processing.max_rate_percent),
            reporter,
        )
        rates[number] = rate
        if rate is not None:
            _report_block_rate(reporter, number, total, fragments[number], rate, "ускорен")

    return clips, rates


async def _retempo_block(
    number: int,
    fragments: Sequence[Fragment],
    clips: list[Clip],
    settings: Settings,
    base_rate: int,
    limits: tuple[int, int],
    reporter: Reporter,
) -> int | None:
    """Переозвучивает блок в новом темпе, чтобы он влез в своё окно.

    ``limits`` — допустимый темп в процентах (нижняя и верхняя граница).
    Возвращает применённый темп или None, если темп остался базовым.
    """
    fragment = fragments[number]
    low, high = limits
    rate = base_rate

    for _ in range(_FIT_ATTEMPTS):
        duration = clips[number].duration
        if duration <= _allowed_duration(fragment.window):
            break

        target = _required_rate_percent(rate, duration, fragment.window)
        target = max(low, min(target, high))
        if target <= rate:
            break
        rate = target
        reporter.update(
            f"Блок {number + 1}/{len(fragments)} ({_format_time(fragment.start)}): "
            f"окно {fragment.window:.1f} с, "
            f"ускоряю до {format_rate_percent(rate)}"
        )
        clips[number] = _apply_pauses(
            fragment,
            await _synthesize_text(
                fragment.text,
                settings.voice.with_rate_percent(rate),
                number + 1,
                len(fragments),
                settings.processing.retries,
                reporter,
            ),
            fill_window=settings.processing.block_align == "auto",
        )

    return rate if rate != base_rate else None


def _report_block_rate(
    reporter: Reporter,
    number: int,
    total: int,
    fragment: Fragment,
    rate: int,
    action: str,
) -> None:
    """Сообщает, что блок переозвучен в другом темпе."""
    reporter.stage(
        f"Блок {number + 1}/{total} ({_format_time(fragment.start)}): "
        f"окно {fragment.window:.1f} с, {action} до {format_rate_percent(rate)}"
    )


def _placement_start(
    mode: str,
    fragment: Fragment,
    duration: float,
    fits: bool,
    position: float,
) -> float:
    """Куда поставить озвученный блок внутри его окна (не раньше ``position``).

    ``auto`` повторяет поведение синхронного перевода: пока речи хватает
    времени, она звучит с начала блока; если блок пришлось ускорять, речь
    прижимается к концу окна — переводчик догоняет оратора к концу фразы.
    """
    if mode == "end" or (mode == "auto" and not fits):
        wanted = fragment.end - duration
    else:
        wanted = fragment.start
    return max(position, wanted)


def _report_fit(
    reporter: Reporter,
    total: int,
    sped_up: int,
    overflow: int,
    shifted: int,
    pauses: int = 0,
) -> None:
    """Сообщает, как разошлись блоки с таймкодом."""
    message = f"В обычном темпе: {total - sped_up} из {total} блок(ов)"
    if sped_up:
        message += f", ускорено: {sped_up}"
    if overflow:
        message += f", не влезло даже на пределе темпа: {overflow}"
    reporter.stage(message)
    if pauses:
        reporter.stage(
            f"Пауз внутри блоков: {pauses} — они возвращаются тишиной там, где в "
            "окне остаётся место: реплики не забегают вперёд своих таймкодов"
        )
    if shifted:
        reporter.stage(
            f"Блоков, начавшихся позже своего таймкода: {shifted} — их сдвинули "
            "предыдущие; увеличьте processing.max_rate_percent или задайте более "
            "быстрый voice.rate, чтобы ускорять сильнее"
        )


async def _synthesize_text(
    text: str,
    voice: VoiceConfig,
    number: int,
    total: int,
    retries: int,
    reporter: Reporter,
) -> Clip:
    """Озвучивает текст, показывая прогресс загрузки и повторы при сбоях."""
    step_bytes = 32 * 1024
    reported = 0

    def on_bytes(size: int) -> None:
        nonlocal reported
        if size - reported < step_bytes:
            return
        reported = size
        reporter.update(
            f"Фрагмент {number}/{total}: {size / 1024:.0f} КБ, темп {voice.rate}"
        )

    def on_retry(attempt: int, attempts: int, delay: float, error: TtsError) -> None:
        reporter.stage(
            f"Фрагмент {number}/{total}: {error}; "
            f"повтор {attempt}/{attempts} через {delay:.0f} с"
        )

    speech = await synthesize(
        text,
        voice,
        on_bytes=on_bytes,
        retries=retries,
        on_retry=on_retry,
    )
    # edge-tts добавляет к реплике служебную тишину: около 0,25 с в начале и до
    # секунды в конце. Без обрезки она съедает паузы и сдвигает озвучку.
    end = speech.end + _TRIM_TAIL_SECONDS if speech.end > 0 else None
    audio = trim(speech.audio, speech.start, end)
    # Отметки слов пересчитываем от начала обрезанных данных: по ним внутрь
    # блока возвращаются паузы субтитров.
    words = tuple(max(0.0, start - speech.start) for start in speech.words)
    return Clip(audio=audio, words=words)


def _base_rate_percent(voice: VoiceConfig, reporter: Reporter) -> int | None:
    """Темп речи из настроек; None, если он задан не в процентах."""
    try:
        return voice.rate_percent
    except ValueError:
        reporter.stage(
            f"Темп {voice.rate!r} задан не в процентах — ускорять реплики не буду"
        )
        return None


def _allowed_duration(window: float) -> float:
    """Сколько секунд может длиться реплика, чтобы считаться уложившейся."""
    return window * (1 + _FIT_TOLERANCE)


def _required_rate_percent(rate_percent: int, duration: float, window: float) -> int:
    """Считает темп, при котором реплика займёт свой блок.

    Длительность речи примерно обратно пропорциональна темпу, поэтому пересчёт
    идёт через коэффициент (1 + rate/100): например, при длительности 3 с и
    блоке 2 с нужен темп +50 %. Округление вверх — чтобы реплика точно влезла.
    """
    factor = 1 + rate_percent / 100
    return math.ceil((factor * duration / window - 1) * 100)


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
