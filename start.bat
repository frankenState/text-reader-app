@echo off
setlocal
cd /d "%~dp0"
title Local Voice

if not exist ".venv\Scripts\python.exe" (
    echo The Python environment is missing. Follow "First-time setup" in README.md:
    echo     uv python install 3.12
    echo     uv sync
    pause
    exit /b 1
)

if not exist "models\kokoro-v1.0.onnx" (
    echo Downloading the voice model once ^(~340 MB^)...
    ".venv\Scripts\python.exe" scripts\fetch_model.py
    if errorlevel 1 (
        echo Model download failed.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" run.py %*
if errorlevel 1 (
    echo.
    echo The server exited with an error. See the messages above.
    pause
)
endlocal
