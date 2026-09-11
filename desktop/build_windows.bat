@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0.."

if /i "%~1"=="--help" goto :help
if not "%~1"=="" (
    echo [ERROR] Unknown argument: %~1
    exit /b 2
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is required.
    exit /b 1
)
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PyInstaller is required. Install requirements-production.txt.
    exit /b 1
)
where pnpm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pnpm is required.
    exit /b 1
)
where cargo >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Rust/Cargo is required.
    exit /b 1
)

for /f %%I in ('git rev-parse HEAD') do set "SOURCE_COMMIT=%%I"
if not defined SOURCE_COMMIT (
    echo [ERROR] Cannot resolve the source Git commit.
    exit /b 1
)
for /f "delims=" %%I in ('git status --porcelain^=v1 --untracked-files^=no') do (
    echo [ERROR] Tracked worktree is dirty. Commit release inputs before building.
    git status --short --untracked-files=no
    exit /b 1
)
set "OFFICEAGENT_SOURCE_COMMIT=%SOURCE_COMMIT%"

echo [1/4] Building frozen backend for %SOURCE_COMMIT%...
python -m PyInstaller office_agent.spec --clean --noconfirm
if errorlevel 1 exit /b 1

echo [2/4] Creating backend release manifest...
python desktop\release_manifest.py create --artifact-dir dist\OfficeAgent --source-commit %OFFICEAGENT_SOURCE_COMMIT%
if errorlevel 1 exit /b 1

echo [3/4] Verifying backend release manifest...
python desktop\release_manifest.py verify --artifact-dir dist\OfficeAgent --expected-commit %OFFICEAGENT_SOURCE_COMMIT%
if errorlevel 1 exit /b 1

echo [4/4] Building Tauri installer...
python desktop\tauri_source_guard.py -- pnpm --dir desktop-client run tauri:build
if errorlevel 1 exit /b 1

for /f "delims=" %%I in ('git status --porcelain^=v1 --untracked-files^=no') do (
    echo [ERROR] Release build changed tracked files.
    git status --short --untracked-files=no
    exit /b 1
)

echo [OK] Release chain completed for %SOURCE_COMMIT%.
exit /b 0

:help
echo Usage: desktop\build_windows.bat
echo.
echo Builds the frozen backend, writes and verifies its full-file SHA-256
echo manifest for the current clean Git HEAD, then builds the Tauri installer.
exit /b 0
