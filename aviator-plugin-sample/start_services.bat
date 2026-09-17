@echo off
title Aviator All-in-One Service Launcher
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\START_ALL_SERVICES.ps1"
if %ERRORLEVEL% NEQ 0 pause
