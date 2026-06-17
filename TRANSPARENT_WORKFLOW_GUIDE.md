# Transparent Workflow System - Implementation Guide

## 🎯 Overview

This implementation adds a **transparent, human-in-the-loop workflow** to the Aviator platform. Every step is visible to the user, with approval checkpoints at critical decision points.

## ✨ Key Features

### 1. **Step-by-Step Visibility**
- Real-time timeline showing all workflow phases
- WebSocket updates for live progress tracking
- Detailed information at each step (data, timestamps, status)

### 2. **Human Approval Checkpoints**

#### Checkpoint 1: Operation Type Classification
- System analyzes ticket and classifies as: Code Modification, Code Addition, Bug Fix, or Refactoring
- User must approve before proceeding

#### Checkpoint 2: File Selection
- Shows all localized files with:
  - Confidence scores (0-100%)
  - Reasoning for each file
  - Method names and line ranges
  - Pre-selected high-confidence files
- User can:
  - ✓ Check/uncheck files
  - ➕ Add more files via folder tree explorer
  - 📁 Browse entire repository with expand/collapse UI

#### Checkpoint 3: Impact Analysis Review
- Displays blast-radius metrics:
  - Direct callers count
  - Transitive dependencies
  - Affected tests
  - Risk level (low/medium/high)
  - API breaking changes warning
- User reviews before patch generation

#### Checkpoint 4: Final Patch Review (Future)
- View generated patches
- See test results
- Approve or reject changes

### 3. **File Explorer with Tree UI**
- Folder hierarchy with ➕ icons to expand
- Checkboxes to select files
- Visual distinction between files and folders
- Search capability (future)

## 📁 File Structure

### Backend Files

```
chatbot/backend/
├── main.py                    # FastAPI server with transparent workflow endpoints
├── workflow_models.py         # Pydantic models for workflow state
└── workflow_manager.py        # Core workflow orchestration logic
```

### Frontend Files

```
chatbot/frontend/src/
├── App.jsx                           # Main app with workflow integration
├── App.css                           # Styles for main app
└── components/
    ├── TransparentWorkflow.jsx       # Main workflow component
    └── TransparentWorkflow.css       # Workflow styling
```

## 🔧 Technical Architecture

### Backend Architecture

#### 1. Workflow Models (`workflow_models.py`)

```python
# Core Enums
WorkflowPhase:
  - IDLE, CLASSIFICATION, LOCALIZATION, IMPACT_ANALYSIS
  - HUMAN_REVIEW_FILES, CONTEXT_LOADING, PATCH_GENERATION
  - VALIDATION, HUMAN_FINAL_REVIEW, COMMITTING
  - COMPLETED, FAILED

OperationType:
  - CODE_MODIFICATION
  - CODE_ADDITION
  - BUG_FIX
  - REFACTORING

# Data Models
FileCandidate:
  - path: str
  - score: float (0.0-1.0)
  - reason: str
  - method_name: Optional[str]
  - start_line, end_line: Optional[int]
  - selected: bool

ImpactAnalysis:
  - direct_callers: int
  - transitive_dependents: int
  - affected_tests: int
  - risk_level: str (low/medium/high)
  - breaks_api: bool

WorkflowState:
  - workflow_id, ticket_id, ticket_description
  - current_phase: WorkflowPhase
  - operation_type: Optional[OperationType]
  - candidate_files: List[FileCandidate]
  - selected_files: List[str]
  - impact_analysis: Optional[ImpactAnalysis]
  - approval flags: operation_approved, files_approved, final_approved
  - steps: List[WorkflowStep] (audit trail)
  - timestamps
```

#### 2. Workflow Manager (`workflow_manager.py`)

Key methods:
- `create_workflow()` - Initialize new workflow
- `classify_operation()` - Determine operation type (uses LLM)
- `localize_files()` - Find relevant files (hybrid AST+Graph+Keyword)
- `analyze_impact()` - Calculate blast-radius
- `add_step()` - Record step and notify via WebSocket
- `approve_operation()` - User approves classification
- `approve_files()` - User approves file selection

#### 3. API Endpoints (`main.py`)

```python
POST /api/workflow/transparent/start
  → Start workflow, return workflow_id

POST /api/workflow/transparent/approve-operation
  → User approves operation type, move to localization

POST /api/workflow/transparent/approve-files
  → User approves files, move to impact analysis

GET /api/workflow/transparent/{workflow_id}
  → Get current workflow state

GET /api/workflow/transparent/{workflow_id}/steps
  → Get all workflow steps

WebSocket /ws/workflow/{workflow_id}
  → Real-time updates as workflow progresses
```

### Frontend Architecture

#### 1. TransparentWorkflow Component

State management:
```javascript
- workflowId: string
- workflowState: WorkflowState object
- steps: Array of WorkflowStep
- selectedFiles: Array of file paths
- showFileExplorer: boolean
- ws: WebSocket connection
```

Key sections:
- **Header**: Shows ticket ID and current phase badge
- **Timeline**: Vertical timeline with all steps (past and current)
- **Approval Cards**: Interactive cards for user decisions
- **File Selection**: Grid of file candidates with checkboxes
- **Impact Analysis**: Metrics dashboard with risk visualization
- **File Explorer Modal**: Tree view for adding files

#### 2. Real-time Updates

```javascript
WebSocket flow:
1. Component mounts → Connect to /ws/workflow/{id}
2. Backend sends update → { type, phase, status, message, data }
3. Frontend updates:
   - Add step to timeline
   - Refresh workflow state
   - Show approval cards if needed
```

## 🚀 How to Use

### Start Backend

```powershell
cd aviator-plugin-sample\chatbot\backend
python main.py
# Backend runs on http://localhost:8000
```

### Start Frontend

```powershell
cd aviator-plugin-sample\chatbot\frontend
npm run dev
# Frontend runs on http://localhost:3002
```

### Workflow Steps

1. **Add Project**
   - Click ➕ in left panel
   - Enter project name and path
   - Select project

2. **Start Workflow**
   - Enter ticket description, e.g.:
     - "Implement user authentication feature"
     - "Fix null pointer exception in PaymentService"
     - "test_document_migration_errors.py"
   - Press Enter or click ▶

3. **Approve Operation Type**
   - Review classification (e.g., "code_modification")
   - Click "✓ Approve & Continue"

4. **Select Files**
   - Review localized files with confidence scores
   - Check/uncheck files as needed
   - Click "➕ Add More Files" to browse repository
   - Click "✓ Approve X Files"

5. **Review Impact**
   - See blast-radius metrics
   - Note risk level and affected components
   - Workflow auto-continues (or can add approval here)

6. **Review Final Patch** (Future)
   - See diff, test results
   - Approve or reject

## 🎨 UI Elements

### Timeline Step States

- **Running**: Blue marker, pulsing animation
- **Completed**: Green marker, static
- **Waiting Approval**: Orange marker, pulsing
- **Failed**: Red marker, static

### Confidence Score Colors

- **90-100%**: Green (High confidence)
- **70-89%**: Light green
- **50-69%**: Yellow (Medium)
- **30-49%**: Orange (Low)
- **0-29%**: Red (Very low)

### Risk Level Colors

- **Low**: Green border
- **Medium**: Orange border
- **High**: Red border

## 🔄 Integration Points

### Current (Mock Data)

For now, the system uses mock data:
- `classify_operation()` uses simple keyword heuristics
- `localize_files()` returns hardcoded FileCandidate objects
- `analyze_impact()` returns mock ImpactAnalysis

### Future (Real Integration)

Replace with:
1. **Classification**: Call ADT Aviator LLM with ticket description
2. **Localization**: Use Phase 2 Hybrid Localizer:
   - Keyword search (60% weight)
   - AST symbol search (30% weight)
   - Graph centrality (5% weight)
   - Semantic search (5% weight)
3. **Impact Analysis**: Use aviator-platform query engine:
   - `callers()` for direct callers
   - Recursive dependency graph traversal
   - Test file matching
4. **File Tree**: Fetch actual repository structure via backend

## 📊 Example Workflow

```
User: "Implement email validation for user registration"

1. CLASSIFICATION (5s)
   → Analyzing ticket...
   → Classified as: CODE_MODIFICATION
   ⏸️  CHECKPOINT: User approves

2. LOCALIZATION (10s)
   → Running hybrid localization...
   → Found 4 candidates:
     ✓ UserService.java (92% - contains registration logic)
     ✓ UserRepository.java (85% - data persistence)
     ☐ UserController.java (78% - REST endpoint)
     ☐ EmailValidator.java (42% - may not be relevant)
   ⏸️  CHECKPOINT: User selects 2 files

3. IMPACT_ANALYSIS (3s)
   → Analyzing blast-radius...
   → Direct callers: 3
   → Transitive deps: 12
   → Affected tests: 8
   → Risk level: MEDIUM

4. PATCH_GENERATION (30s)
   → Loading context for 2 files...
   → Generating AST-safe patches...
   → Validating syntax...
   ⏸️  CHECKPOINT: User reviews diff

5. COMPLETED
   → Changes applied successfully
   → 2 files modified
   → All tests passing
```

## 🛠️ Development Notes

### Adding New Phases

1. Add to `WorkflowPhase` enum in `workflow_models.py`
2. Add handler method in `workflow_manager.py`
3. Update `PHASE_LABELS` in `TransparentWorkflow.jsx`
4. Add phase-specific UI components if needed

### Adding New Checkpoint

1. Add approval flag to `WorkflowState` (e.g., `context_approved`)
2. In workflow manager, set `status="waiting_approval"` at checkpoint
3. Add approval endpoint in `main.py`
4. Add approval UI card in `TransparentWorkflow.jsx`

### Testing

```powershell
# Backend tests
cd aviator-plugin-sample\chatbot\backend
pytest test_workflow.py

# Frontend (manual testing for now)
# 1. Start both backend and frontend
# 2. Add a project
# 3. Enter various ticket descriptions
# 4. Test all approval flows
# 5. Test file selection and explorer
```

## 🚧 Known Limitations

1. **Mock Data**: Localization and classification use mock logic
2. **No Persistence**: Workflows stored in memory (lost on restart)
3. **No File Tree API**: File explorer uses mock structure
4. **No Patch Generation**: Phase not implemented yet
5. **Single User**: No multi-user support

## 🎯 Next Steps

### Immediate
1. Integrate real localization engine (Phase 2)
2. Connect to ADT Aviator LLM for classification
3. Implement file tree API endpoint
4. Add persistence (SQLite or Redis)

### Short-term
1. Add patch generation with AST-safe modifications
2. Implement validation phase (syntax, build, tests)
3. Add search/filter in file explorer
4. Show code snippets for file candidates

### Long-term
1. Multi-user support with authentication
2. Workflow history and replay
3. Custom approval policies per project
4. Integration with git for commits
5. Rollback capability
6. Agent customization per user

## 📝 Code Quality

- TypeScript conversion (frontend)
- Add comprehensive tests
- Error boundary components
- Loading skeletons
- Offline mode detection
- WebSocket reconnection logic

## 🎓 Architecture Principles

This implementation follows the core principles:

1. **AST-First**: All code analysis uses tree-sitter AST
2. **Patch-First**: Modifications will be AST-safe patches
3. **Repository-Intelligence-First**: Localization happens before generation
4. **Human-in-the-Loop**: Approval at every critical decision
5. **Transparency**: Every action visible to user
6. **Fail-Safe**: User can stop workflow at any checkpoint

---

**Last Updated**: December 2024
**Version**: 1.0.0 (Transparent Workflow MVP)
