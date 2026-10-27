@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title MASB Telegram Bot

if not exist ".env" (
    echo [ERROR] Не найден .env.
    echo Скопируй .env.example в .env и вставь TELEGRAM_BOT_TOKEN.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Создаю виртуальное окружение...
    py -3 -m venv .venv
    if errorlevel 1 goto :error
)

set "PY=.venv\Scripts\python.exe"
"%PY%" -m pip install --upgrade pip
if errorlevel 1 goto :error
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :error
"%PY%" main.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo [ERROR] Запуск MASB завершился с ошибкой. Проверь сообщения выше.
pause
exit /b 1
