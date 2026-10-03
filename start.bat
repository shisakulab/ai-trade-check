@echo off
chcp 65001 > nul
cd /d "%~dp0"
where python > nul 2>&1 && goto run_python
where py > nul 2>&1 && goto run_py
echo Python が見つかりません。https://www.python.org/ からインストールしてください。
pause
exit /b 1

:run_python
python server.py
goto end

:run_py
py -3 server.py

:end
pause
