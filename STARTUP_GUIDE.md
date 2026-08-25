# 🚀 Aviator System Startup Guide

This document contains the PowerShell commands required to start all components of the Aviator architecture manually. You can copy and run these blocks directly in your PowerShell terminal.

---

### 1. 🗄️ Docker Database & Message Broker Services
Ensure your Docker containers (`pgvector_db`, `aviator-neo4j`, `aviator-qdrant`, and `rabbitmq_mgmt`) are running. If they are not running, you can start the databases with:
```powershell
cd "C:\Users\dmadgani\Desktop\My_Aviator"
.\start_services.bat
```
*(Note: You can also start your `aviator-full-stac` compose stack directly from Docker Desktop).*

---

### 2. ⚙️ Aviator Backend (Port 8002)
This is the main Python backend (`chatbot/backend/main.py`) running via `uv`.

Open a new PowerShell window and run:
```powershell
cd "C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample"

$env:POSTGRES_CONNECTION="postgresql://postgres:postgres@127.0.0.1:5433/postgres"
$env:BROKER_URL="amqp://admin:admin_pass@localhost:5672/"
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"
$env:content_system="sample"
$env:BIND_PORT="8002"
$env:PYTHONPATH="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample;C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src;C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\src"
$env:PYTHONIOENCODING="utf-8"
$env:NEO4J_PASSWORD="aviator-dev"
$env:TRACE_MODE="true"

uv run python C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend\main.py
```

---

### 3. 🧠 Celery RAG Worker
This background worker processes embeddings and workspace summaries asynchronously.

Open a new PowerShell window and run:
```powershell
cd "C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt"

$env:NEO4J_PASSWORD="aviator-dev"
$env:POSTGRES_CONNECTION="postgresql://postgres:postgres@localhost:5433/postgres"
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"
$env:BROKER_URL="amqp://guest:guest@localhost:5672/"

.venv\Scripts\activate.ps1
celery -A src.aviator.celery worker --loglevel=info -P solo
```

---

### 4. 🖥️ ContentBridge Chatbot Frontend (Port 3000)
This is the Vite/React UI.

Open a new PowerShell window and run:
```powershell
cd "C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\frontend"
npm run dev
```

---

## 🧭 System Overview
- **Frontend URL**: `http://localhost:3000`
- **Backend API**: `http://localhost:8002`
- **PostgreSQL**: `localhost:5433`
- **Neo4j**: `localhost:7474` (HTTP) / `7687` (Bolt)
- **Qdrant**: `localhost:6333`
- **RabbitMQ**: `localhost:5672`
