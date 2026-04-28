@echo off
chcp 65001 > nul
echo ============================================
echo  Import bills from bills.txt
echo ============================================
echo Fill bills.txt with bill numbers first.
echo.
pause
python import_bills.py
pause
