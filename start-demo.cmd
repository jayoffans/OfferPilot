@echo off
setlocal
pushd "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo Install uv, Python 3.11 and supported Node.js/npm first.
    echo See docs\LOCAL_SETUP.md, then run start-demo.cmd again.
    set "demo_result=1"
    goto finish
)
uv run --project backend --locked --no-sync --no-env-file python scripts/local/manage.py demo %*
set "demo_result=%errorlevel%"
:finish
popd
if not "%demo_result%"=="0" if not "%demo_result%"=="130" if not defined CI if not defined OFFERPILOT_NO_PAUSE pause
exit /b %demo_result%
