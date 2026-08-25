@echo off
echo ================================================================================
echo Starting Aviator ADT RAG Worker (Celery)
echo ================================================================================
echo.

REM Set environment variables
set NEO4J_PASSWORD=aviator-dev
set POSTGRES_CONNECTION=postgresql://postgres:postgres@localhost:5433/postgres
set GOOGLE_APPLICATION_CREDENTIALS=C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json
set BROKER_URL=amqp://guest:guest@localhost:5672/

cd /d C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt
call .venv\Scripts\activate.bat

celery -A src.aviator.celery worker --loglevel=info -P solo
