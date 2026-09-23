@echo off
setlocal enableextensions enabledelayedexpansion
cd /d "%~dp0"
title RB/HO Jun-vs-Dec Spread-Analyse

echo ==============================================================================
echo   RB/HO Jun-vs-Dec Crack-Spread - Analyse starten
echo ==============================================================================
echo.
echo   Dieses Fenster nicht schliessen. Der Lauf meldet sich, wenn er fertig ist.
echo.

rem --------------------------------------------------------------------------
rem 1) Python suchen
rem --------------------------------------------------------------------------
set "PY="
call :findpy
if defined PY goto :haspy

echo   Kein Python gefunden. Es wird versucht, Python zu installieren.
echo   (Benutzerinstallation, keine Administratorrechte noetig.)
echo.

where winget >nul 2>&1
if %errorlevel% equ 0 (
    echo   Installation ueber winget ...
    winget install -e --id Python.Python.3.12 --scope user --silent ^
        --accept-package-agreements --accept-source-agreements
    call :findpy
    if defined PY goto :haspy
)

echo   winget nicht verfuegbar oder fehlgeschlagen. Direkter Download ...
set "PYSETUP=%TEMP%\python-setup-3.12.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%PYSETUP%' } catch { exit 1 }"
if exist "%PYSETUP%" (
    echo   Installiere Python ...
    "%PYSETUP%" /passive InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
    del "%PYSETUP%" >nul 2>&1
    call :findpy
    if defined PY goto :haspy
)

echo.
echo   ---------------------------------------------------------------------
echo   Python konnte nicht automatisch installiert werden.
echo   Das ist auf verwalteten Firmenrechnern normal - die IT-Richtlinie
echo   verbietet Installationen.
echo.
echo   Zwei Auswege:
echo     a) IT bitten, Python 3.12 zu installieren (Benutzerinstallation reicht).
echo     b) Den Excel-Weg nehmen: bloomberg\batches\pull_block1_....xlsx oeffnen,
echo        Strg+Alt+F9, danach Inhalte einfuegen als Werte, speichern und die
echo        Datei auf den Analyse-PC kopieren.
echo   ---------------------------------------------------------------------
echo.
pause
exit /b 1

:haspy
echo   Python gefunden: !PY!
echo.
!PY! "tools\one_click.py" %*
set "RC=!errorlevel!"
echo.
if "!RC!"=="0" (
    echo   Fertig. Einzelheiten stehen in output\last_run.txt
) else (
    echo   Der Lauf wurde mit Fehler !RC! beendet. Die Meldungen oben nennen die Ursache.
)
echo.
pause
exit /b !RC!

rem --------------------------------------------------------------------------
rem Python-Suche: py-Launcher, PATH, dann die ueblichen Installationspfade.
rem (Nach einer frischen Installation kennt dieses Fenster den neuen PATH noch
rem  nicht - deshalb werden die Pfade direkt geprueft.)
rem --------------------------------------------------------------------------
:findpy
py -3 -c "import sys" >nul 2>&1 && (set "PY=py -3" & exit /b 0)
python -c "import sys" >nul 2>&1 && (set "PY=python" & exit /b 0)
for %%D in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
) do (
    if exist %%D (set "PY=%%D" & exit /b 0)
)
exit /b 1
