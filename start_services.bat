@echo off
echo ================================================================================
echo Starting ALL Required Services for Aviator ADT
echo ================================================================================
echo.

REM Set docker path
set DOCKER=C:\Program Files\Docker\Docker\resources\bin\docker.exe

REM Check Docker is running
"%DOCKER%" ps >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not running. Please start Docker Desktop first.
    pause
    exit /b 1
)

echo [1/3] Starting PostgreSQL (pgvector) on port 5433...
"%DOCKER%" run -d ^
    --name pgvector_db ^
    -e POSTGRES_USER=postgres ^
    -e POSTGRES_PASSWORD=postgres ^
    -e POSTGRES_DB=postgres ^
    -p 5433:5432 ^
    -v pgdata:/var/lib/postgresql/data ^
    pgvector/pgvector:pg15 >nul 2>&1
echo       Done (or already running).

echo [2/3] Starting Neo4j on port 7687...
"%DOCKER%" run -d ^
    --name aviator-neo4j ^
    -e NEO4J_AUTH=neo4j/aviator-dev ^
    -p 7474:7474 ^
    -p 7687:7687 ^
    -v neo4j-data:/data ^
    neo4j:5.20-community >nul 2>&1
echo       Done (or already running).

echo.
echo [3/3] Waiting for databases to be ready...
timeout /t 10 /nobreak >nul

echo.
echo ================================================================================
echo All services started! You can now run start_backend.bat
echo ================================================================================
echo.
echo   PostgreSQL:  localhost:5433
echo   Neo4j HTTP:  http://localhost:7474  (user: neo4j, pass: aviator-dev)
echo   Neo4j Bolt:  bolt://localhost:7687
echo.
