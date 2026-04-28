@echo off
chcp 65001 > nul
echo ============================================
echo  WARNING: This will DELETE all records!
echo ============================================
echo.
set /p confirm=Type YES to confirm: 
if /i "%confirm%"=="YES" (
    python -c "import sqlite3,os; db='data/ri.db'; conn=sqlite3.connect(db); conn.execute('DELETE FROM initiatives'); conn.commit(); conn.close(); print('Database cleared.')"
) else ( echo Cancelled. )
pause
