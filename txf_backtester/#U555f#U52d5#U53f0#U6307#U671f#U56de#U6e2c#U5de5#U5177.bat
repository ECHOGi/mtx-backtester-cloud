@echo off
setlocal
cd /d "%~dp0"
title TXF Backtester Launcher

set "PYTHON_CMD="

echo ========================================
echo TXF Backtester Launcher
echo ========================================
echo.
echo Project folder:
echo %CD%
echo.
echo CSV data folder should be:
echo %CD%\data
echo.

if not exist "app.py" (
    echo ERROR: app.py was not found.
    echo Please run this file from the extracted txf_backtester folder.
    echo Do NOT run it directly inside the ZIP file.
    echo.
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo ERROR: requirements.txt was not found.
    echo Please check that the project was fully extracted.
    echo.
    pause
    exit /b 1
)

if not exist "data" (
    echo WARNING: data folder was not found.
    echo Creating data folder now.
    mkdir "data"
    echo Please put 2015_fut.csv to 2025_fut.csv into the data folder.
    echo.
)

echo Checking Python 3.12...
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if "%PYTHON_CMD%"=="" (
    py -3.12 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3.12"
)

if "%PYTHON_CMD%"=="" (
    echo ERROR: Python 3.12 was not found.
    echo Please install Python 3.12 64-bit and check "Add python.exe to PATH".
    echo.
    echo Current python version, if any:
    python --version
    echo.
    pause
    exit /b 1
)

echo Using Python command: %PYTHON_CMD%
%PYTHON_CMD% -m streamlit --version >nul 2>&1
if errorlevel 1 (
    echo Streamlit is not ready. Installing required packages...
    echo This may take a few minutes the first time.
    echo.
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Package installation failed.
        echo Please take a screenshot of this window and send it to AI for checking.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo Starting TXF Backtester...
echo If the browser does not open, manually visit:
echo http://localhost:8501
echo.
echo IMPORTANT: Keep this black window open while using the backtester.
echo Close this window only when you want to stop the platform.
echo.

start "" /min cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8501"
%PYTHON_CMD% -m streamlit run app.py --server.headless true

echo.
echo TXF Backtester has stopped.
echo.
pause
