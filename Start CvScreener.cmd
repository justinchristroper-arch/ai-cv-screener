@echo off
rem Starts CvScreener: first-time setup check, Docker Desktop, the database,
rem migrations, the AI model check, backend and frontend, then opens
rem http://localhost:5173. The work is in scripts\start-cvscreener.ps1.
rem -ExecutionPolicy Bypass applies to this one PowerShell process only;
rem no system setting is changed.
title CvScreener
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-cvscreener.ps1"
