@echo off
echo ================================================================================
echo Starting Aviator ADT Backend for PHASE 1 Testing
echo ================================================================================
echo.

REM Set environment variables
set NEO4J_PASSWORD=aviator-dev
set POSTGRES_CONNECTION=postgresql://postgres:postgres@localhost:5433/postgres
set GOOGLE_APPLICATION_CREDENTIALS=C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json

echo Environment Variables Set:
echo   NEO4J_PASSWORD: ********
echo   POSTGRES_CONNECTION: localhost:5433
echo   GOOGLE_APPLICATION_CREDENTIALS: Set
echo.

REM Navigate to aviator_adt
cd /d C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt

echo Starting Aviator ADT Backend on port 3001...
echo.

REM Activate virtual environment and start backend
call .venv\Scripts\activate.bat
uvicorn src.aviator.main:app --host 0.0.0.0 --port 3001 --reload
