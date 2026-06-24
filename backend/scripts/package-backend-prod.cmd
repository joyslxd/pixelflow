@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "BACKEND_DIR=%%~fI"
for %%I in ("%BACKEND_DIR%\..") do set "PROJECT_DIR=%%~fI"

set "PACKAGE_NAME=pixelflow-backend-prod.tar.gz"
if not "%~1"=="" set "PACKAGE_NAME=%~1"

set "PACKAGE_DIR=%BACKEND_DIR%\dist"
set "PACKAGE_PATH=%PACKAGE_DIR%\%PACKAGE_NAME%"

where tar >nul 2>nul
if errorlevel 1 (
  echo [ERROR] tar command was not found.
  echo Please run this script on Windows 10/11, or install Git for Windows and use package-backend-prod.sh with Git Bash.
  exit /b 1
)

if not exist "%PACKAGE_DIR%" mkdir "%PACKAGE_DIR%"
if exist "%PACKAGE_PATH%" del /f /q "%PACKAGE_PATH%"

pushd "%PROJECT_DIR%" || exit /b 1

tar ^
  --exclude=*/__pycache__ ^
  --exclude=*.pyc ^
  --exclude=*.pyo ^
  --exclude=.DS_Store ^
  -czf "%PACKAGE_PATH%" ^
  backend/app ^
  backend/pixelflow ^
  backend/packages ^
  backend/skills ^
  backend/config.prod.yml ^
  backend/pyproject.toml ^
  backend/uv.lock ^
  backend/.python-version ^
  backend/Makefile ^
  backend/langgraph.json ^
  backend/README.md

set "TAR_EXIT=%ERRORLEVEL%"
popd

if not "%TAR_EXIT%"=="0" exit /b %TAR_EXIT%

echo PixelFlow prod backend package created:
echo %PACKAGE_PATH%
