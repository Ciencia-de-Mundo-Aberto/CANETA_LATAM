@echo off
rem CANETA - instalador para Windows: clique duas vezes neste arquivo.
rem Roda o install.ps1 que esta na mesma pasta; ele baixa a CANETA do GitHub.
title Instalador da CANETA
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo A instalacao falhou. Veja as mensagens acima.
  pause
)
