# Launch Fidelity Control Center Web Server and open in default browser
Write-Host "Starting Fidelity Control Center Web Terminal..." -ForegroundColor Cyan
Start-Process "http://localhost:8505"
uv run python -m fidelity_control.server
