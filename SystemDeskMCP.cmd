@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "REQ_FILE=%SCRIPT_DIR%requirements.txt"
set "SERVER_FILE=%SCRIPT_DIR%src\systemdesk_mcp_server.py"

rem Redirect setup stdout to stderr so MCP stdio output on stdout is not polluted.
python -m venv "%VENV_DIR%" 1>&2 || exit /b

call "%VENV_DIR%\Scripts\activate.bat" 1>&2 || exit /b

python -m pip install -r "%REQ_FILE%" 1>&2 || exit /b

python "%SERVER_FILE%"

endlocal
