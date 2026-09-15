@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "PAKLOC_OLLAMA_MODEL=gemma4:latest"
call "%~dp0START_V3A.bat"
