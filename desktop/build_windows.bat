@echo off
chcp 65001 >nul
echo ==========================================
echo   OfficeAgent v0.51.1 - Windows Build
echo ==========================================
echo.

:: 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

:: 检查PyInstaller
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

:: 安装依赖
echo Installing dependencies...
pip install -r requirements.txt

:: 打包
echo.
echo Building OfficeAgent.exe...
pyinstaller office_agent.spec --clean --noconfirm

if errorlevel 1 (
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   Build complete!
echo   Output: dist\OfficeAgent\
echo ==========================================
echo.
echo To create the desktop installer, use Tauri:
echo   cd desktop-client ^&^& npm run tauri:build
echo.
pause
