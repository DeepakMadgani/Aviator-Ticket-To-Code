# Aviator Indexing & Startup Guide

## 🕵️‍♂️ How I Monitored the Live Progress
When I started your FastAPI backend server in the background, it runs as an active task on your machine. I used one of my tools (`manage_task`) to read the live terminal logs of that specific process. 

I was able to see the exact Python `logger.info` outputs being printed by `main.py`, which showed:
`INFO:main:Indexing progress: 1197/1197 (100%)`
Because I saw it successfully cross the 100% mark for file parsing without crashing, I knew it had safely moved into the RAG vector and Neo4j graph generation phases!

---

## ⚠️ Critical Mistakes to Avoid (And How We Fixed Them)

When we first started troubleshooting, the indexing process was failing silently or crashing for two main reasons. I have permanently fixed both of these in your codebase, but here is what happened so you don't run into it again:

> [!WARNING]
> **Mistake 1: Missing System Paths (`ModuleNotFoundError`)**
> **The Issue:** The backend crashed immediately on startup because it couldn't find the `aviator` module. The `ticket_to_code` agent imports rely heavily on both `aviator-platform` and `aviator_adt/src`.
> **The Fix:** I modified `chatbot/backend/main.py` to dynamically resolve its absolute path and forcefully inject `aviator-platform` and `aviator_adt/src` into the Python `sys.path` at the very top of the script. This ensures the backend will always find the modules, regardless of how or where you start the server from.

> [!CAUTION]
> **Mistake 2: Relative `.env` Loading (Missing Neo4j Password)**
> **The Issue:** Neo4j indexing was failing because `load_dotenv()` was looking for the `.env` file in the current terminal directory rather than the backend directory. This caused the connection to fail with a `Password required` error.
> **The Fix:** I updated `main.py` to use an absolute path for environment variables: `env_path = Path(__file__).parent / ".env"`. Now it will flawlessly find your database passwords no matter what folder your terminal is opened in.

> [!IMPORTANT]
> **Mistake 3: UI Showing Empty Projects List ("Not showing already indexed things")**
> **The Issue:** The React UI failed to show the previously indexed projects (like CC4E), resulting in an empty list on the screen. Initially, I incorrectly diagnosed this as a frontend JSON parsing bug in `App.jsx` and introduced unnecessary code edits that broke the UI further.
> **The Fix:** The backend API actually returns a perfectly formatted JSON array, and the original frontend code correctly parses it using `Array.isArray(data)`. The true fix was to **completely revert** the unnecessary code changes to `App.jsx`. Always verify the raw API JSON output (`curl` or direct browser inspection) before altering working frontend logic!

---

## 🚀 How to Start the Aviator System

To get the entire system running properly for indexing and chatting, you need to start **4 separate components**. 

Open a new PowerShell terminal for each of the following:

### 1. The Databases (Docker)
You must have your containers running (PostgreSQL, Neo4j, Qdrant, RabbitMQ).
```powershell
cd C:\Users\dmadgani\Desktop\My_Aviator
.\start_services.bat
```

### 2. The FastAPI Backend (Port 8002)
This handles the API requests, runs the ticket agents, and executes the indexing pipeline.
```powershell
# Set environment variables
$env:POSTGRES_CONNECTION="postgresql://postgres:postgres@127.0.0.1:5433/postgres"
$env:BROKER_URL="amqp://admin:admin_pass@localhost:5672/"
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"
$env:content_system="sample"
$env:BIND_PORT="8002"
$env:NEO4J_PASSWORD="aviator-dev"
$env:TRACE_MODE="true"
$env:PYTHONPATH="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src;C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\src"

# Start Server
$py = "C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt\.venv\Scripts\python.exe"
cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend
& $py -m uvicorn main:app --host 0.0.0.0 --port 8002 --log-level info
```

### 3. The Frontend React UI (Port 3000)
This serves the interactive web interface.
```powershell
cd C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\frontend
npm run dev
```

### 4. The Celery Worker (Background RAG processor)
This worker handles asynchronous queue tasks for embeddings.
```powershell
cd C:\Users\dmadgani\Desktop\My_Aviator\aviator_adt

# Set environment variables
$env:NEO4J_PASSWORD="aviator-dev"
$env:POSTGRES_CONNECTION="postgresql://postgres:postgres@localhost:5433/postgres"
$env:GOOGLE_APPLICATION_CREDENTIALS="C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\otl-cs-csai.json"
$env:BROKER_URL="amqp://guest:guest@localhost:5672/"

# Start Worker
.venv\Scripts\activate.ps1
celery -A src.aviator.celery worker --loglevel=info -P solo
```

---

## 🛑 How to STOP Everything Cleanly

If you ever need to forcefully kill all lingering backend, frontend, and Celery processes so you can start fresh, run this command in any PowerShell window:

```powershell
# 1. Kill the API and UI by closing their ports
Get-NetTCPConnection -LocalPort 8002, 3000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

# 2. Kill all background Celery RAG Workers
Get-WmiObject Win32_Process -Filter "name='python.exe' or name='celery.exe'" | Where-Object { $_.CommandLine -match "celery" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
```
*(Note: To stop the databases, just run `docker-compose down` from inside the `aviator_adt` directory).*

---

## 🧹 How to Manually Remove an Index
If you ever need to completely wipe a project's index and start fresh, you must clear data from three different places (as well as reset the UI state). 

If you need to wipe a project located at `C:\CC4E`, you must do the following:

1. **Remove SQLite Database**: Delete the hidden folder at `C:\CC4E\.aviator`. This stores the local AST (Abstract Syntax Tree) data.
2. **Clear Neo4j Nodes**: Connect to Neo4j and delete all graph nodes associated with the workspace path `C:\CC4E`. (This is programmatically done via `Neo4jStore.clear_repository(path)`).
3. **Clear RAG Embeddings**: Delete all vector chunks from PostgreSQL/pgvector where the `source_project` matches `C:\CC4E`. (This is programmatically done via `VectorStoreManager`).
4. **Reset UI State**: In `chatbot/backend/projects.json`, change the project's `"status": "indexed"` back to `"status": "ready"`.

---

## ⚙️ How Indexing Works Internally
When you click "Index" in the UI, the backend kicks off a heavy multi-stage pipeline:

1. **File System Parsing (AST Generation)**: It scans the raw codebase files and uses Tree-sitter to parse the code into an Abstract Syntax Tree (AST). It discovers all classes, methods, and variables, and saves them into a local SQLite database (`.aviator/index.db`) located directly inside the target repository.
2. **Dependency Resolution**: It scans the local SQLite DB to map out how all the files and methods interact with each other (e.g., finding exactly which function calls which other function).
3. **Semantic Embedding (RAG)**: It takes large chunks of code, converts them into AI vector embeddings, and pushes them into your Vector Database (PostgreSQL/pgvector). This enables "Semantic Search" so the chatbot can find code based on its meaning, rather than just exact keywords.
4. **Graph Building (Neo4j)**: It pushes all the extracted symbols (nodes) and their dependencies (edges) into the Neo4j Graph Database. This allows the chatbot to write Cypher queries to explore the codebase architecture exactly like a 3D map.
5. **Brain Generation**: Finally, it generates a `generated_directory_brain.json` file inside the `.aviator` directory, which acts as a highly compressed directory map for the LLM to quickly understand the folder structure without scanning the real disk.
