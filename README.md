# srt-to-audio

Озвучивает субтитры в формате **SRT** и сохраняет результат в **MP3**.
Синтез речи работает через [edge-tts](https://github.com/rany2/edge-tts) —
бесплатный онлайн-сервис Microsoft Edge, ключи API не нужны (только интернет).
По умолчанию используется мужской русский голос `ru-RU-DmitryNeural`,
другие варианты — в разделе [«Голоса»](#голоса).

## Установка

Требуется Python 3.9+ и доступ в интернет (речь синтезируется онлайн через
сервис edge-tts). Скрипт одинаково работает на **macOS**, **Windows 11** и
**Ubuntu/Debian**, дополнительных программ (ffmpeg и т.п.) не нужно.

### macOS

```bash
cd srt-to-audio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

### Windows 11 (PowerShell)

```powershell
cd srt-to-audio
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Если PowerShell блокирует активацию окружения, один раз выполните
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` — либо не активируйте
окружение, а запускайте скрипт напрямую:
`.venv\Scripts\python.exe main.py`.

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
cd srt-to-audio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Быстрый старт

1. Положите файл субтитров в папку `input` (например, `input/lesson.srt`).
2. Запустите из корня проекта:

```bash
python main.py          # macOS / Linux / Windows (в активированном окружении)
python3 main.py         # macOS / Linux, если окружение не активировано
```

Без активации окружения:

```bash
.venv/bin/python main.py            # macOS / Linux
.venv\Scripts\python.exe main.py    # Windows
```

3. Готовый файл появится в папке `output` (`output/lesson.mp3`).

## Использование

```bash
python main.py                            # озвучить все .srt из папки input
python main.py -f lesson.srt              # только один файл
python main.py -f lesson.srt -o voice     # своё имя результата (output/voice.mp3)
python main.py --voice ru-RU-SvetlanaNeural # выбрать голос (женский)
python main.py --rate "+10%" --pitch "-20Hz"   # темп и тон (кавычки — для PowerShell)
python main.py --list-voices ru           # список доступных голосов
python main.py --overwrite                # перезаписать существующие mp3
```

Все ключи:

| Ключ | Назначение |
| --- | --- |
| `-f`, `--file` | имя файла из папки `input` или полный путь |
| `-o`, `--out` | имя выходного файла без расширения (только для одного входа) |
| `--voice` | голос edge-tts, например `ru-RU-SvetlanaNeural` |
| `--rate` | темп речи: `+10%`, `-20%` |
| `--volume` | громкость: `+0%`, `-50%` |
| `--pitch` | тон: `+0Hz`, `-20Hz` |
| `--overwrite` | разрешить перезапись готовых mp3 |
| `--list-voices [ЯЗЫК]` | показать голоса (можно отфильтровать по языку) |
| `--config` | путь к альтернативному файлу настроек |

Коды возврата: `0` — успех, `1` — часть файлов не удалось обработать,
`2` — ошибка настроек или аргументов, `130` — прервано пользователем.

## Голоса

По умолчанию используется мужской голос **`ru-RU-DmitryNeural`**.
Полный список доступных голосов:

```bash
python main.py --list-voices        # все голоса (их больше 300)
python main.py --list-voices ru     # только русские
```

Русские голоса:

| Голос | Пол | Комментарий |
| --- | --- | --- |
| `ru-RU-DmitryNeural` | мужской | **по умолчанию** |
| `ru-RU-SvetlanaNeural` | женский | женский русский голос |

Мультиязычные голоса понимают текст и на русском, но тембр «иностранный» —
их удобно использовать, чтобы получить другую манеру чтения:

| Голос | Пол | Голос | Пол |
| --- | --- | --- | --- |
| `en-US-AndrewMultilingualNeural` | мужской | `en-US-AvaMultilingualNeural` | женский |
| `en-US-BrianMultilingualNeural` | мужской | `en-US-EmmaMultilingualNeural` | женский |
| `en-AU-WilliamMultilingualNeural` | мужской | `fr-FR-VivienneMultilingualNeural` | женский |
| `de-DE-FlorianMultilingualNeural` | мужской | `de-DE-SeraphinaMultilingualNeural` | женский |
| `fr-FR-RemyMultilingualNeural` | мужской | `pt-BR-ThalitaMultilingualNeural` | женский |
| `it-IT-GiuseppeMultilingualNeural` | мужской | `ko-KR-HyunsuMultilingualNeural` | мужской |

Выбрать голос можно разово (`--voice ...`) или постоянно — в `config.json`,
в поле `voice.name`.

## Настройки

Все параметры хранятся в `config.json` в корне проекта:

```json
{
  "paths": {
    "input_dir": "input",
    "output_dir": "output",
    "output_extension": ".mp3"
  },
  "voice": {
    "name": "ru-RU-DmitryNeural",
    "rate": "+0%",
    "volume": "+0%",
    "pitch": "+0Hz"
  },
  "processing": {
    "segment_joiner": "\n",
    "chunk_max_chars": 3000,
    "overwrite": false,
    "retries": 4
  }
}
```

| Параметр | Описание |
| --- | --- |
| `paths.input_dir` / `paths.output_dir` | папки входа и выхода; относительные пути считаются от расположения `config.json` |
| `paths.output_extension` | расширение результата (по умолчанию `.mp3`) |
| `voice.name` | голос синтеза (`python main.py --list-voices ru`) |
| `voice.rate` / `voice.volume` / `voice.pitch` | темп, громкость, тон |
| `processing.segment_joiner` | чем склеивать реплики субтитров в один текст |
| `processing.chunk_max_chars` | размер фрагмента для запроса к сервису (больше — меньше запросов) |
| `processing.overwrite` | перезаписывать ли существующие файлы |
| `processing.retries` | сколько раз повторять запрос при сбое сервиса (пауза между попытками растёт: 2, 4, 8, 15 секунд) |

Значения из командной строки имеют приоритет над `config.json`.
Пропущенные параметры берут значения по умолчанию, неизвестные — считаются ошибкой.

## Как это работает

1. Из папки `input` читаются файлы `.srt` (кодировки UTF-8, UTF-8 с BOM, CP1251).
2. Субтитры разбираются на реплики, из текста убирается разметка (`<i>`, `{\an8}` и т.п.).
3. Реплики склеиваются в сплошной текст и режутся на фрагменты по границам предложений.
4. Каждый фрагмент озвучивается через edge-tts, mp3-данные склеиваются в один файл.
5. Результат сохраняется в `output`.

Озвучивается именно текст субтитров: временные метки в аудио не переносятся,
паузы между репликами задаются знаком препинания в исходном тексте.

## Структура проекта

```
main.py                   точка входа
config.json               настройки
requirements.txt          зависимости
srt_to_audio/
    config.py             загрузка и проверка настроек
    srt_parser.py         чтение и разбор SRT
    tts.py                обёртка над edge-tts
    converter.py          логика «srt -> mp3»
    cli.py                аргументы, прогресс, вывод ошибок
input/                    исходные субтитры
output/                   готовые mp3
```

## Если что-то не работает

| Сообщение | Что делать |
| --- | --- |
| `Папка с субтитрами не найдена` | создайте папку `input` или поправьте `paths.input_dir` |
| `В папке ... нет файлов .srt` | положите файл субтитров в `input` |
| `Не удалось получить аудио от edge-tts` | проверьте интернет и доступность сервиса, повторите запуск |
| `Сервис edge-tts не вернул аудио` | временная блокировка сервиса (слишком частые запросы): подождите пару минут и повторите запуск |
| `Файл уже существует` | удалите mp3 или добавьте `--overwrite` |
| `Не удалось определить кодировку` | пересохраните субтитры в UTF-8 |
| `python: command not found` | используйте `python3` (macOS/Linux) или `py` (Windows) |
| `No module named venv` (Debian/Ubuntu) | `sudo apt install python3-venv` |
| `ModuleNotFoundError: edge_tts` | окружение не активировано: `pip install -r requirements.txt` |

## Кроссплатформенность

- Пути собираются через `pathlib`, разделители каталогов подставляются системой;
  в `config.json` можно писать и относительные, и абсолютные пути.
- Вывод принудительно переводится в UTF-8, поэтому русский текст не ломает
  консоль Windows; при перенаправлении вывода в файл кодировка также UTF-8.
- Индикация прогресса перерисовывает одну строку только в терминале; при
  перенаправлении в файл/пайп печатаются лишь ключевые шаги.
- Внешних утилит нет: синтез идёт по сети, файлы пишутся средствами Python.
