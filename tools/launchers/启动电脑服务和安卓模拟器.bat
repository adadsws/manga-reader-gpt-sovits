@echo off
setlocal
chcp 65001 >nul
title 漫画朗读 - 启动电脑服务和安卓模拟器
if /i "%~1"=="check" goto check
if /i "%~1"=="stop" goto stop
set "reader_saved_no_pause=%READER_NO_PAUSE%"
set "READER_NO_PAUSE=1"
call "%~dp0仅启动安卓模拟器.bat" keep-service
set "reader_emulator_exit=%errorlevel%"
set "READER_NO_PAUSE=%reader_saved_no_pause%"
if not "%reader_emulator_exit%"=="0" exit /b %reader_emulator_exit%
call "%~dp0仅启动电脑服务.bat"
exit /b %errorlevel%
:check
call "%~dp0仅启动安卓模拟器.bat" check
if errorlevel 1 exit /b %errorlevel%
call "%~dp0仅启动电脑服务.bat" check
exit /b %errorlevel%
:stop
call "%~dp0仅启动电脑服务.bat" stop
exit /b %errorlevel%
