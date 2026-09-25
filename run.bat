@echo off
py -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python mexc_signal_bot.py
pause
