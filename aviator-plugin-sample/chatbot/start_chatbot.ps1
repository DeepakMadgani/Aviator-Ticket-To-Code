# ============================================================
# START AVIATOR CHATBOT (Backend + Frontend)
# ============================================================

$env:NEO4J_PASSWORD = "aviator-dev"
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"
$env:POSTGRES_CONNECTION = "postgresql://postgres:postgres@127.0.0.1:5433/postgres"
$env:BROKER_URL = "amqp://admin:admin_pass@localhost:5672/"
$env:content_system = "sample"
$env:PYTHONIOENCODING = "utf-8"

$BACKEND_DIR = "C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend"
$FRONTEND_DIR = "C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\frontend"
$SRC_DIR = "C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src;C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample"
$BACKEND_PORT = 8002

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host " AVIATOR CHATBOT STARTUP" -ForegroundColor Cyan
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""

# Check Docker containers
Write-Host "Checking Docker containers..." -ForegroundColor Yellow
$containers = docker ps --format "{{.Names}}" 2>$null
if ($containers -match "pgvector_db") { Write-Host "  pgvector_db    running" -ForegroundColor Green }
else { Write-Host "  pgvector_db    NOT RUNNING - start with: docker start pgvector_db" -ForegroundColor Red }
if ($containers -match "aviator-neo4j") { Write-Host "  aviator-neo4j  running" -ForegroundColor Green }
else { Write-Host "  aviator-neo4j  NOT RUNNING - start with: docker start aviator-neo4j" -ForegroundColor Yellow }

Write-Host ""
Write-Host "Starting backend on port $BACKEND_PORT..." -ForegroundColor Yellow

# Free port if needed
Get-NetTCPConnection -LocalPort $BACKEND_PORT -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# Start backend in a new window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "`$env:GOOGLE_APPLICATION_CREDENTIALS='$($env:GOOGLE_APPLICATION_CREDENTIALS)'; " +
    "`$env:NEO4J_PASSWORD='$($env:NEO4J_PASSWORD)'; " +
    "`$env:POSTGRES_CONNECTION='$($env:POSTGRES_CONNECTION)'; " +
    "`$env:BROKER_URL='$($env:BROKER_URL)'; " +
    "`$env:content_system='$($env:content_system)'; " +
    "`$env:PYTHONIOENCODING='utf-8'; " +
    "`$env:PYTHONPATH='$SRC_DIR'; " +
    "Set-Location 'C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample'; " +
    "uv run python C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend\main.py"
)

Write-Host "  Backend starting in new window..." -ForegroundColor Green
Start-Sleep -Seconds 3

Write-Host ""
Write-Host "Starting frontend on port 3000..." -ForegroundColor Yellow

# Start frontend in a new window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$FRONTEND_DIR'; npm.cmd run dev"
)

Write-Host "  Frontend starting in new window..." -ForegroundColor Green
Start-Sleep -Seconds 2

Write-Host ""
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host " CHATBOT READY" -ForegroundColor Cyan
Write-Host "  Frontend:  http://localhost:3000" -ForegroundColor White
Write-Host "  Backend:   http://localhost:$BACKEND_PORT" -ForegroundColor White
Write-Host "  Health:    http://localhost:$BACKEND_PORT/api/health" -ForegroundColor White
Write-Host "===========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "HOW TO GIVE A TICKET:" -ForegroundColor Yellow
Write-Host "  1. Select a project in the left panel" -ForegroundColor White
Write-Host "  2. Type your task in the chat, e.g.:" -ForegroundColor White
Write-Host "     'implement add area endpoint in AreaService.java'" -ForegroundColor Gray
Write-Host "     'fix null pointer in PaymentService'" -ForegroundColor Gray
Write-Host "     'add logging to database queries'" -ForegroundColor Gray
Write-Host "  3. Watch live execution in Transparent Workflow view" -ForegroundColor White
Write-Host ""
