@echo off
setlocal

REM Path to Inno Setup Compiler (update if your path is different)
set ISCC_PATH="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

REM Path to your .iss script
set ISS_FILE="windows_build_scripts\inno_setup.iss"
set TMP_ISS_FILE="windows_build_scripts\inno_setup_tmp.iss"

REM Get version from git
for /f "delims=" %%v in ('git describe --tags') do set APPVER=%%v

REM Create a temp .iss file with the version replaced
(for /f "usebackq delims=" %%l in (%ISS_FILE%) do (
    set "line=%%l"
    setlocal enabledelayedexpansion
    if "!line!" neq "" (
        echo(!line:#define MyAppVersion "1.0"=#define MyAppVersion "%APPVER%"!
    ) else (
        echo.
    )
    endlocal
)) > %TMP_ISS_FILE%

REM Build the installer using the temp .iss file
%ISCC_PATH% %TMP_ISS_FILE%

REM Clean up temp file
del %TMP_ISS_FILE%

if %ERRORLEVEL%==0 (
    echo Installer built successfully.
) else (
    echo Installer build failed.
)

endlocal