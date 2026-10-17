#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Запуск srt-to-audio одной командой (macOS / Linux).
#
#  Сам находит python3, при первом запуске создаёт окружение .venv и ставит
#  зависимости, дальше просто запускает main.py с переданными аргументами:
#      ./srt2mp3.sh
#      ./srt2mp3.sh -f movie.srt -o voice
#      ./srt2mp3.sh --list-voices ru
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT/.venv/bin/python"
REQUIREMENTS="$ROOT/requirements.txt"

cd "$ROOT"

if [ ! -x "$VENV_PY" ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "[srt2mp3] Python 3 не найден. Установите его и запустите снова." >&2
        exit 1
    fi
    echo "[srt2mp3] Первый запуск: создаю окружение .venv (это может занять минуту) ..."
    if ! python3 -m venv "$ROOT/.venv"; then
        echo "[srt2mp3] Не удалось создать окружение." >&2
        echo "[srt2mp3] В Debian/Ubuntu нужен пакет: sudo apt install python3-venv" >&2
        exit 1
    fi
fi

if [ ! -x "$VENV_PY" ]; then
    echo "[srt2mp3] Не удалось подготовить окружение .venv." >&2
    echo "[srt2mp3] В Windows используйте srt2mp3.cmd — этот скрипт для macOS/Linux." >&2
    exit 1
fi

# Зависимости проверяем быстро и без сети, если они уже стоят.
if ! "$VENV_PY" -c "import edge_tts" >/dev/null 2>&1; then
    echo "[srt2mp3] Устанавливаю зависимости из requirements.txt (это может занять минуту) ..."
    if ! "$VENV_PY" -m pip install --disable-pip-version-check --quiet -r "$REQUIREMENTS"; then
        echo "[srt2mp3] Не удалось установить зависимости. Проверьте интернет." >&2
        exit 1
    fi
fi

exec "$VENV_PY" "$ROOT/main.py" "$@"
