@echo off
rem Insert Coin: sign the newest update in Downloads and copy it to the wallet card.
cd /d "%~dp0"
py tools\sign_update.py
