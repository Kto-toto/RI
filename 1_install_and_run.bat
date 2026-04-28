@echo off
chcp 65001 > nul
echo ============================================
echo  Step 1: Installing dependencies...
echo ============================================
pip install -r requirements.txt
echo.
echo ============================================
echo  Step 2: Starting Regulatory Intelligence...
echo  Open browser: http://localhost:5000
echo ============================================
echo.
python app.py
pause
