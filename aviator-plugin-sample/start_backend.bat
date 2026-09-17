@echo off
title Aviator Backend (Uvicorn)
echo Starting Uvicorn backend on port 8002...
set GOOGLE_APPLICATION_CREDENTIALS=C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json
set NEO4J_PASSWORD=aviator-dev
set POSTGRES_CONNECTION=postgresql://postgres:postgres@127.0.0.1:5433/postgres
set BROKER_URL=amqp://admin:admin_pass@localhost:5672/
set content_system=sample
set BIND_PORT=8002
set TRACE_MODE=true
set PYTHONPATH=C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src;C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\src
cd /d C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend
"C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\.venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8002 --log-level info
pause
