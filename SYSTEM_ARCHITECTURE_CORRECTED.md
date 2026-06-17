# SYSTEM ARCHITECTURE - CORRECTED

We have TWO complementary systems that work together:

## 1. FULL AUTONOMOUS SYSTEM: `src/ticket_to_code/`
**Location:** `aviator-plugin-sample/src/ticket_to_code/`
**Purpose:** Complete autonomous workflow with LangGraph
**Features:**
- Investigation Agent (triage)
- Ticket Analyzer  
- Planning Agent
- RAG Engine (codebase search)
- Code Generator
- Build & Test Executor
**API:** `/ticket-to-code/*` endpoints
**Usage:** Submit ticket → Fully autonomous processing → Get generated code

## 2. CHAT UI SYSTEM: `chatbot/backend/`
**Location:** `aviator-plugin-sample/chatbot/backend/`
**Purpose:** Simplified chat interface with human-in-the-loop
**Features:**
- Step-by-step workflow with approvals
- Real-time WebSocket updates
- Manual file selection
- Interactive code review
**API:** `/api/*` endpoints
**Usage:** Chat with AI → Approve each step → Apply changes

## BOTH USE AVIATOR_ADT FOR REAL LLM!

```
aviator_adt (port 8080)
    ↓ LLM Service
    ├─→ ticket_to_code system (autonomous)
    └─→ chatbot backend (interactive)
```

## WHAT I FIXED:
✅ Integrated ticket_to_code router into chatbot backend
✅ Updated workflow_manager to use REAL LLM from aviator.services.llm
✅ Created .env file for aviator_adt configuration
✅ Started PostgreSQL and RabbitMQ in Docker

## NEXT: Start aviator_adt and test!
