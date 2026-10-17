@echo off
rem ---------------------------------------------------------------------------
rem  Запуск srt-to-audio одной командой (Windows).
rem
rem  Сам находит Python, при первом запуске создаёт окружение .venv и ставит
rem  зависимости, дальше просто запускает main.py с переданными аргументами:
rem      srt2mp3.cmd
rem      srt2mp3.cmd -f movie.srt -o voice
rem      srt2mp3.cmd --list-voices ru
rem ---------------------------------------------------------------------------
setlocal

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
set "REQUIREMENTS=%ROOT%requirements.txt"

cd /d "%ROOT%" || goto :error

if exist "%VENV_PY%" goto :deps

rem --- Ищем базовый Python: сначала лаунчер py, затем python из PATH ---
set "BASE_PY="
where py >nul 2>nul && set "BASE_PY=py -3"
if not defined BASE_PY (
    where python >nul 2>nul && set "BASE_PY=python"
)
if not defined BASE_PY (
    echo [srt2mp3] Python не найден. Установите его с https://python.org и запустите снова.
    goto :error
)

echo [srt2mp3] Первый запуск: создаю окружение .venv (это может занять минуту) ...
%BASE_PY% -m venv "%ROOT%.venv"
if errorlevel 1 (
    echo [srt2mp3] Не удалось создать окружение .venv.
    goto :error
)
if not exist "%VENV_PY%" (
    echo [srt2mp3] Файл %VENV_PY% не появился: проверьте установку Python.
    goto :error
)

:deps
rem --- Проверяем, что зависимости на месте (быстро и без сети при их наличии) ---
"%VENV_PY%" -c "import edge_tts" >nul 2>nul
if not errorlevel 1 goto :run

echo [srt2mp3] Устанавливаю зависимости из requirements.txt (это может занять минуту) ...
"%VENV_PY%" -m pip install --disable-pip-version-check --quiet -r "%REQUIREMENTS%"
if errorlevel 1 (
    echo [srt2mp3] Не удалось установить зависимости. Проверьте интернет и повторите запуск.
    goto :error
)

:run
"%VENV_PY%" "%ROOT%main.py" %*
exit /b %ERRORLEVEL%

:error
exit /b 1
