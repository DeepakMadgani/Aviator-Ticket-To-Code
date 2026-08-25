cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample
Write-Host "Checking dependencies..." -ForegroundColor Cyan
uv pip install gitpython neo4j 2>$null

Write-Host "Starting Aviator Backend..." -ForegroundColor Green
$env:POSTGRES_CONNECTION="postgresql://postgres:postgres@127.0.0.1:5433/postgres"
$env:BROKER_URL="amqp://guest:guest@localhost:5672/"
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"
$env:content_system="sample"
$env:BIND_PORT=8002
$env:PYTHONPATH="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample;C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src;C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\src"
$env:PYTHONIOENCODING="utf-8"
$env:NEO4J_PASSWORD="aviator-dev"
$env:TRACE_MODE="true"
uv run python C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend\main.py

