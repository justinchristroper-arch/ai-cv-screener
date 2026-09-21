@echo off
rem Stops what Start CvScreener launched, and nothing else. The database's
rem data volume is never touched. The work is in scripts\start-cvscreener.ps1.
rem -ExecutionPolicy Bypass applies to this one PowerShell process only;
rem no system setting is changed.
title CvScreener - stop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-cvscreener.ps1" -Stop
