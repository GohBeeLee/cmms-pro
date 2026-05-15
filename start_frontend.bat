@echo off
echo ==========================================
echo   CMMS Frontend Setup & Start
echo ==========================================

cd /d "%~dp0frontend"

:: Check Node
node --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Node.js not found.
    echo Please install Node.js 20 from https://nodejs.org
    pause
    exit /b 1
)

echo Node found:
node --version
echo npm version:
npm --version

:: Install dependencies if needed
if not exist "node_modules\" (
    echo.
    echo Installing Node packages (first time, may take a few minutes)...
    npm install
    if errorlevel 1 (
        echo ERROR: npm install failed. See error above.
        pause
        exit /b 1
    )
)

:: Start frontend
echo.
echo ==========================================
echo   Frontend running at http://localhost:5173
echo   Press CTRL+C to stop
echo ==========================================
echo.
npm run dev
if errorlevel 1 (
    echo.
    echo ERROR: Frontend failed to start. See error above.
    pause
)
pause