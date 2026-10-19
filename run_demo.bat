@echo off
rem run_demo.bat - run scripts/run_demo.sh from Windows cmd / PowerShell via Git Bash.
rem Avoids C:\Windows\System32\bash.exe, which is WSL and would not see this Python install.
setlocal
cd /d "%~dp0"

set "BASH_EXE="
for %%B in (
    "%ProgramFiles%\Git\bin\bash.exe"
    "%ProgramW6432%\Git\bin\bash.exe"
    "%LOCALAPPDATA%\Programs\Git\bin\bash.exe"
    "C:\msys64\usr\bin\bash.exe"
) do if not defined BASH_EXE if exist %%B set "BASH_EXE=%%~B"

if not defined BASH_EXE (
    echo [run_demo] Git Bash not found. Install Git for Windows or run: bash scripts/run_demo.sh
    exit /b 1
)

"%BASH_EXE%" scripts/run_demo.sh %*
exit /b %ERRORLEVEL%
