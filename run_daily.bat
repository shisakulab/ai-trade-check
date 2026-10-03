@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
where python > nul 2>&1 && goto run_python
where py > nul 2>&1 && goto run_py
echo Python が見つかりません。https://www.python.org/ からインストールしてください。 >> run.log

exit /b 1

:run_python
python main.py >> run.log 2>&1
goto end

:run_py
py -3 main.py >> run.log 2>&1

:end

