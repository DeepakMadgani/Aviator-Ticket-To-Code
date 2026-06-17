# 🔄 TWO AVIATOR SYSTEMS - ARCHITECTURE COMPARISON

## YOU WERE RIGHT! There are TWO different systems:

---

## 1️⃣ **aviator_adt** (LangGraph/LangChain) - FULL AGENT FRAMEWORK
**This is what you remember using before!**

### 📍 Location: `aviator_adt/src/aviator/`

### 🧠 Technology Stack:
- ✅ **LangGraph** - State machine for agent workflows
- ✅ **LangChain** - LLM abstraction and tools
- ✅ **PostgreSQL** - Persistent state storage with checkpointing
- ✅ **Vector Store** - RAG (Retrieval Augmented Generation)
- ✅ **MCP (Model Context Protocol)** - Tool integration
- ✅ **Celery** - Background task processing
- ✅ **FastAPI** - Production-grade API server

### 📦 Key Components:
```python
from langgraph.graph import StateGraph                    # Agent state machine
from langchain_core.language_models.chat_models import BaseChatModel
from langchain.agents.middleware import ModelResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # Persistent state
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command

# Tools
from aviator.tools.rag import rag_query                   # RAG search
from aviator.tools.summarize import generate_summary      # Summarization
from aviator.tools.chart_generator import generate_vega_lite_chart
from aviator.tools.table_generator import generate_markdown_table
```

### 🎯 What It Does:
- **Multi-turn conversations** with memory
- **RAG-based retrieval** from knowledge bases
- **Tool calling** (charts, tables, summaries)
- **Streaming responses** via WebSocket
- **Persistent state** across sessions
- **A2A (Agent-to-Agent)** communication
- **Plugin system** for extensibility

### 📂 Structure:
```
aviator_adt/src/aviator/
├── graph.py              ← LangGraph agent definition
├── models.py             ← Pydantic state models
├── celery.py             ← Background workers
├── main.py               ← FastAPI server
├── api/
│   ├── v1.py             ← REST endpoints
│   └── websockets.py     ← WebSocket streaming
├── tools/
│   ├── rag.py            ← RAG search
│   ├── summarize.py      ← Summarization
│   └── ...
├── database/
│   └── checkpointer.py   ← PostgreSQL state persistence
└── vector_store/         ← Vector embeddings
```

---

## 2️⃣ **aviator-platform** (Pure Python) - LIGHTWEIGHT CODE INDEXER
**This is what we're currently using for ticket-to-code!**

### 📍 Location: `aviator-platform/aviator_core/`

### 🧠 Technology Stack:
- ✅ **Tree-sitter** - AST parsing (Java)
- ✅ **SQLite** - Local code index database
- ✅ **Neo4j** (Optional) - Graph database for complex queries
- ✅ **Qdrant** (Optional) - Vector search
- ✅ **sentence-transformers** - Embeddings

### 📦 Key Components:
```python
from aviator_core.indexer import index_repository          # Index Java code
from aviator_core.parsers.java_parser import JavaParser    # AST parsing
from aviator_core.storage.sqlite_store import SqliteStore  # Local DB
from aviator_core.localizer import HybridLocalizer         # Find relevant files
from aviator_core.llm import ADTAviatorClient              # LLM integration
from aviator_core.storage.neo4j_store import Neo4jStore    # Graph DB
from aviator_core.storage.vector_store import VectorStore  # Vector search
```

### 🎯 What It Does:
- **Parse Java code** into AST (Abstract Syntax Tree)
- **Extract symbols** (classes, methods, fields)
- **Build relationship graph** (calls, extends, implements)
- **Spring Framework intelligence** (detect controllers, services, repositories)
- **Localize relevant files** for a ticket
- **Generate code patches** using LLM
- **Apply AST-aware transformations**

### 📂 Structure:
```
aviator-platform/aviator_core/
├── indexer.py                  ← Repository indexing
├── models.py                   ← Data models (Symbol, Edge)
├── parsers/
│   ├── java_parser.py          ← Tree-sitter Java parser
│   └── enhanced_patch_engine.py ← AST transformations
├── localizer/
│   ├── hybrid_localizer.py     ← File localization
│   ├── spring_filter.py        ← Spring intelligence
│   ├── keyword_search.py       ← FTS5 search
│   └── symbol_search.py        ← Symbol-based search
├── storage/
│   ├── sqlite_store.py         ← Primary storage
│   ├── neo4j_store.py          ← Graph database
│   └── vector_store.py         ← Vector search
└── llm/
    └── adt_client.py           ← ADT LLM client
```

---

## 🤔 **WHY TWO SYSTEMS?**

### **aviator_adt** is for:
- ✅ General-purpose **conversational AI**
- ✅ **RAG-based Q&A** over documents
- ✅ **Multi-domain agents** (not just code)
- ✅ **Production deployments** with state persistence
- ✅ **Complex workflows** with human-in-the-loop

### **aviator-platform** is for:
- ✅ **Code-specific tasks** (ticket-to-code)
- ✅ **Fast local indexing** (no external dependencies)
- ✅ **Lightweight operations** (SQLite, no PostgreSQL needed)
- ✅ **AST-aware code generation**
- ✅ **Spring Framework intelligence**

---

## 🚀 **CURRENT SETUP (What We're Using):**

```
My_Aviator/
├── aviator_adt/                    ← RECOVERED (LangGraph system)
│   └── src/aviator/                   BUT NOT CURRENTLY USING
│
├── aviator-platform/               ← ✅ ACTIVELY USING
│   └── aviator_core/                  For ticket-to-code workflow
│
└── aviator-plugin-sample/          ← ✅ ACTIVELY USING
    └── chatbot/                       UI + Backend (imports from aviator-platform)
        ├── frontend/                  React UI
        └── backend/                   FastAPI (uses aviator-platform)
            ├── main.py
            └── workflow_manager.py
```

---

## 💡 **DECISION POINT:**

### **Option A: Keep Current Setup (aviator-platform)**
**Pros:**
- ✅ Already working (indexed 1,336 files!)
- ✅ Lightweight (no PostgreSQL, Celery)
- ✅ Code-focused features (Spring intelligence, AST-aware)

**Cons:**
- ❌ Less sophisticated agent capabilities
- ❌ No built-in RAG over general documents
- ❌ No persistent conversation state

### **Option B: Switch to aviator_adt (LangGraph)**
**Pros:**
- ✅ Full agent framework with memory
- ✅ Production-ready architecture
- ✅ Extensible plugin system
- ✅ RAG over knowledge bases

**Cons:**
- ❌ Requires PostgreSQL, Redis, Celery setup
- ❌ More complex (learning curve)
- ❌ Overkill for simple ticket-to-code

### **Option C: Hybrid Approach** 🌟
Use **aviator-platform** for code indexing + **aviator_adt** for agent workflow:
```python
# Use aviator-platform for code analysis
from aviator_core.indexer import index_repository
from aviator_core.localizer import HybridLocalizer

# Use aviator_adt for agent orchestration
from aviator.graph import create_agent_graph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
```

---

## 📋 **RECOMMENDATION:**

For **ticket-to-code MVP**, stick with **aviator-platform** (current setup):
- You've already indexed 1,336 files
- Backend is running
- UI is ready
- All features work (Neo4j, Qdrant, Spring intelligence)

You can add **aviator_adt** features later if needed!

---

**Ready to test a ticket with the current system?** 🎯
