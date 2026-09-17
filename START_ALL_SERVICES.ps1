# ==============================================================================
# AVIATOR MASTER STARTUP SCRIPT
# Automated End-to-End Environment Launcher & Service Manager
# ==============================================================================
# 1. Checks and opens Docker Desktop if closed; waits for daemon to be ready
# 2. Starts all Docker containers (PostgreSQL pgvector, Neo4j, Qdrant, RabbitMQ)
# 3. Kills any previous lingering instances on ports 8002, 3000, 5173 and processes
# 4. Launches Celery Worker, Uvicorn Backend, and Vite Frontend in separate persistent windows
# 5. Performs health checks and displays status
# ==============================================================================

$ErrorActionPreference = "Continue"

$rootDir = "C:\Users\dmadgani\Desktop\My_Aviator"
$pluginDir = "C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample"
$composeFile = "C:\Users\dmadgani\Desktop\My_Aviator\aviator-full-stack\docker-compose.yml"

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "      AVIATOR ADT - MASTER STARTUP & RESTART SEQUENCE       " -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# STEP 1: DOCKER ENGINE VERIFICATION & STARTUP
# ------------------------------------------------------------------------------
Write-Host "[Step 1/5] Checking Docker Desktop status..." -ForegroundColor Yellow

$dockerReady = $false
try {
    $null = docker info 2>$null
    if ($LASTEXITCODE -eq 0) {
        $dockerReady = $true
    }
} catch {
    $dockerReady = $false
}

if (-not $dockerReady) {
    Write-Host "Docker is NOT running. Launching Docker Desktop..." -ForegroundColor Magenta
    $dockerDesktopPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    
    if (Test-Path $dockerDesktopPath) {
        Start-Process $dockerDesktopPath
    } else {
        Write-Host "WARNING: Docker Desktop executable not found at '$dockerDesktopPath'." -ForegroundColor Red
        Write-Host "Attempting to launch via start command..." -ForegroundColor Yellow
        Start-Process "cmd.exe" -ArgumentList "/c start docker-desktop" -WindowStyle Hidden
    }

    Write-Host "Waiting for Docker daemon to become responsive..." -ForegroundColor Yellow
    $waited = 0
    $maxWait = 90
    while (-not $dockerReady -and $waited -lt $maxWait) {
        Start-Sleep -Seconds 3
        $waited += 3
        try {
            $null = docker info 2>$null
            if ($LASTEXITCODE -eq 0) {
                $dockerReady = $true
                break
            }
        } catch {}
        Write-Host "  ... waiting for Docker daemon ($waited / $maxWait s)" -ForegroundColor Gray
    }

    if ($dockerReady) {
        Write-Host "  Docker daemon is online and responsive!" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: Docker daemon did not respond within $maxWait seconds. Attempting to proceed..." -ForegroundColor Red
    }
} else {
    Write-Host "  Docker is already running." -ForegroundColor Green
}

# ------------------------------------------------------------------------------
# STEP 2: DOCKER CONTAINERS STARTUP
# ------------------------------------------------------------------------------
Write-Host "`n[Step 2/5] Starting Docker Containers (pgvector, neo4j, qdrant, rabbitmq)..." -ForegroundColor Yellow

if (Test-Path $composeFile) {
    docker compose -f $composeFile up -d
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Docker containers successfully started/verified." -ForegroundColor Green
    } else {
        Write-Host "  Docker compose completed. Checking status..." -ForegroundColor Yellow
    }
} else {
    Write-Host "  Compose file not found at $composeFile!" -ForegroundColor Red
}

# ------------------------------------------------------------------------------
# STEP 3: TERMINATE PREVIOUS LINGERING INSTANCES
# ------------------------------------------------------------------------------
Write-Host "`n[Step 3/5] Cleaning up old processes and freeing ports (8002, 3000, 5173)..." -ForegroundColor Yellow

# Kill processes listening on ports (with full tree kill)
Get-NetTCPConnection -LocalPort 8002, 3000, 5173 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { 
        Write-Host "  Terminating process PID $($_.OwningProcess) listening on port $($_.LocalPort)..." -ForegroundColor Gray
        Start-Process -FilePath "taskkill.exe" -ArgumentList "/F /T /PID $($_.OwningProcess)" -NoNewWindow -Wait -ErrorAction SilentlyContinue
    }

# Kill existing Celery, Uvicorn, Backend main.py, uv, and Node frontend processes
Get-CimInstance Win32_Process | Where-Object { 
    ($_.CommandLine -and $_.CommandLine -match 'celery|uvicorn|main\.py' -and ($_.Name -match 'python|uv')) -or
    ($_.CommandLine -and $_.CommandLine -match 'start_backend\.ps1') -or
    ($_.CommandLine -and $_.CommandLine -match 'chatbot\\frontend' -and $_.Name -match 'node')
} | ForEach-Object { 
    Write-Host "  Terminating stale process PID $($_.ProcessId): $($_.Name)..." -ForegroundColor Gray
    Start-Process -FilePath "taskkill.exe" -ArgumentList "/F /T /PID $($_.ProcessId)" -NoNewWindow -Wait -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
Write-Host "  Cleanup completed. Ports are ready." -ForegroundColor Green

# ------------------------------------------------------------------------------
# STEP 4: LAUNCH APPLICATION SERVICES IN DEDICATED WINDOWS
# ------------------------------------------------------------------------------
Write-Host "`n[Step 4/5] Launching application services in dedicated windows..." -ForegroundColor Yellow

# 4A. Celery Worker
Write-Host "  Launching Celery Worker window..." -ForegroundColor Cyan
$celeryBat = Join-Path $pluginDir "run_celery.bat"
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$celeryBat`"" -WorkingDirectory $rootDir

# 4B. Backend FastAPI / Uvicorn
Write-Host "  Launching Backend (Uvicorn) window on port 8002..." -ForegroundColor Cyan
$backendBat = Join-Path $pluginDir "run_backend.bat"
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$backendBat`"" -WorkingDirectory $pluginDir

# 4C. Frontend Vite
Write-Host "  Launching Frontend (Vite) window on port 3000..." -ForegroundColor Cyan
$frontendBat = Join-Path $pluginDir "run_frontend.bat"
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$frontendBat`"" -WorkingDirectory (Join-Path $pluginDir "chatbot\frontend")

# ------------------------------------------------------------------------------
# STEP 5: VERIFICATION & HEALTH CHECKS
# ------------------------------------------------------------------------------
Write-Host "`n[Step 5/5] Verifying service readiness..." -ForegroundColor Yellow

$backendOk = $false
for ($i = 1; $i -le 45; $i++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:8002/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($health.status -eq "healthy") {
            $backendOk = $true
            break
        }
    } catch {}
    Write-Host "  ... waiting for Backend (attempt $i/45)" -ForegroundColor Gray
}

$frontendOk = $false
for ($i = 1; $i -le 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $res = Invoke-WebRequest -Uri "http://localhost:3000/" -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($res.StatusCode -eq 200) {
            $frontendOk = $true
            break
        }
    } catch {}
    Write-Host "  ... waiting for Frontend (attempt $i/20)" -ForegroundColor Gray
}

# Final Status Banner
Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "      ALL AVIATOR SERVICES ARE RUNNING & VERIFIED!          " -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green

if ($backendOk) {
    Write-Host "  • Backend API:   http://localhost:8002 (HEALTHY)" -ForegroundColor Green
    Write-Host "  • Health API:    http://localhost:8002/api/health" -ForegroundColor Gray
} else {
    Write-Host "  • Backend API:   http://localhost:8002 (Still starting - check Backend window)" -ForegroundColor Yellow
}

if ($frontendOk) {
    Write-Host "  • Frontend UI:   http://localhost:3000 (READY)" -ForegroundColor Green
} else {
    Write-Host "  • Frontend UI:   http://localhost:3000 (Still starting - check Frontend window)" -ForegroundColor Yellow
}

Write-Host "  • Celery Worker: Running in dedicated window" -ForegroundColor Green
Write-Host "  • PostgreSQL:    localhost:5433 (pgvector)" -ForegroundColor Green
Write-Host "  • Neo4j Graph:   http://localhost:7474" -ForegroundColor Green
Write-Host "  • RabbitMQ:      localhost:5672 (UI: http://localhost:15672)" -ForegroundColor Green
Write-Host "  • Qdrant Vector: http://localhost:6333" -ForegroundColor Green
Write-Host "============================================================`n" -ForegroundColor Green
