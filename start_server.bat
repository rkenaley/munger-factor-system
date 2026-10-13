@echo off
echo Starting Fidelity Control Center...
start http://localhost:8505
uv run python -m fidelity_control.server
pause
