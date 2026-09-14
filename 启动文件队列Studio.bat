@echo off
cd /d "%~dp0studio-file-queue"
set "PAKLOC_OLLAMA_MODEL=gemma4:latest"
call npm start
