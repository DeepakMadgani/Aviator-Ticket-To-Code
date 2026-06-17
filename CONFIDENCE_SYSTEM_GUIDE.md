# 🎯 CONFIDENCE SYSTEM & USER CONTROL
## Complete Guide to Approvals and Manual File Selection

**Date**: May 27, 2026  
**Author**: Deepak Madgani  
**System**: Aviator Autonomous Engineering Platform

---

## 📊 **CONFIDENCE THRESHOLD SYSTEM**

### **Confidence Levels**

| Score | Level | Meaning | Action |
|-------|-------|---------|--------|
| **0.90 - 1.00** | ✅ Very High | AI is very confident | Auto-proceed (no review required) |
| **0.75 - 0.89** | ✅ High | AI is confident | Auto-proceed with notification |
| **0.50 - 0.74** | ⚠️ Medium | AI is uncertain | **Require approval** |
| **0.00 - 0.49** | ❌ Low | AI is not confident | **Require approval + manual selection** |

### **When Approval Triggers**

```python
# Default threshold: 0.75
if localization_result.confidence < 0.75:
    requires_human_review = True
    # Show approval dialog to user
```

**Triggers**:
- ❌ Could not find exact file match in SQLite
- ❌ Multiple files match (ambiguous)
- ❌ No dependency information in Neo4j
- ❌ No execution paths found
- ❌ Impact analysis incomplete
- ⚠️ Files exist but not in expected locations
- ⚠️ Methods found but signatures don't match requirements

---

## 🎛️ **THREE APPROVAL MODES**

### **Mode 1: CONFIDENCE_BASED** (Default, Recommended)

**How It Works**:
```python
mode = ApprovalMode.CONFIDENCE_BASED

if confidence >= 0.75:
    # Auto-proceed
    continue_workflow()
else:
    # Ask user for approval
    show_approval_dialog()
```

**Use Case**: Production environment, balance between speed and safety

**Example**:
```
Ticket: "Add email validation"
Confidence: 0.94 → ✅ Auto-proceeds
Confidence: 0.62 → ⚠️ Asks for approval
```

---

### **Mode 2: ALWAYS** (Maximum Safety)

**How It Works**:
```python
mode = ApprovalMode.ALWAYS

# ALWAYS show approval dialog
# regardless of confidence
show_approval_dialog()
```

**Use Case**: 
- Critical production systems
- Financial/healthcare systems
- First-time using the system
- Training/learning mode

**Example**:
```
Ticket: "Add email validation"
Confidence: 0.98 → ⚠️ Still asks for approval
Confidence: 0.45 → ⚠️ Asks for approval
```

---

### **Mode 3: NEVER** (Fully Autonomous)

**How It Works**:
```python
mode = ApprovalMode.NEVER

# NEVER show approval dialog
# even if confidence is low
# (risky, not recommended for production)
continue_workflow()
```

**Use Case**:
- Development/testing environment only
- Experimental features
- Well-tested tickets
- **NOT recommended for production**

**Example**:
```
Ticket: "Add email validation"
Confidence: 0.98 → ✅ Auto-proceeds
Confidence: 0.25 → ⚠️ Still auto-proceeds (risky!)
```

---

## 🎯 **APPROVAL DIALOG - USER OPTIONS**

When approval is required, user sees this dialog:

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  APPROVE WORKFLOW CHANGES                                     ┃
┃                                                               ┃
┃  Ticket: VE-12345 "Add email validation"                      ┃
┃  Confidence: 0.62 ⚠️ (Medium - Review Required)               ┃
┃                                                               ┃
┃  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ┃
┃                                                               ┃
┃  FILES AI SUGGESTS MODIFYING:                                 ┃
┃                                                               ┃
┃   [✓] src/services/SupplierService.java (MODIFY)             ┃
┃       - Methods: saveSupplier()                               ┃
┃       - Confidence: 0.85                                      ┃
┃       - Reason: Exact match in AST                            ┃
┃                                                               ┃
┃   [✓] src/tests/SupplierServiceTest.java (CREATE)            ┃
┃       - Confidence: 0.90                                      ┃
┃       - Reason: Test file doesn't exist                       ┃
┃                                                               ┃
┃   [✗] src/dto/SupplierDTO.java (MODIFY)                      ┃
┃       - Confidence: 0.45 ⚠️                                   ┃
┃       - Reason: Ambiguous match - might not be needed         ┃
┃       [Remove from plan]                                      ┃
┃                                                               ┃
┃  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ┃
┃                                                               ┃
┃  MANUALLY ADD FILES:                                          ┃
┃                                                               ┃
┃   [+ Add File]  ↓ Select from 147 project files              ┃
┃                                                               ┃
┃   Search: [_______________]                                   ┃
┃                                                               ┃
┃   Recently modified:                                          ┃
┃   □ src/services/ValidationService.java                       ┃
┃   □ src/config/ApplicationConfig.java                         ┃
┃   □ src/utils/EmailValidator.java                            ┃
┃                                                               ┃
┃  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ┃
┃                                                               ┃
┃  IMPACT ANALYSIS:                                             ┃
┃                                                               ┃
┃   Affected APIs:  /api/suppliers                              ┃
┃   Affected Services:  SupplierService                         ┃
┃   Affected Controllers:  SupplierController                   ┃
┃   Database Changes:  No                                       ┃
┃   Migration Required:  No                                     ┃
┃                                                               ┃
┃  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ┃
┃                                                               ┃
┃  EXECUTION PATHS:                                             ┃
┃                                                               ┃
┃   SupplierController → SupplierService → SupplierRepository   ┃
┃   OrderController → OrderService → SupplierService            ┃
┃                                                               ┃
┃  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ┃
┃                                                               ┃
┃  FEEDBACK (Optional):                                         ┃
┃   [____________________________________________]              ┃
┃                                                               ┃
┃  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ┃
┃                                                               ┃
┃  [Approve & Continue]  [Request Changes]  [Cancel Workflow]   ┃
┃                                                               ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

### **User Actions**

#### **1. Approve Suggested Files** ✅
- Check/uncheck files AI suggested
- Remove low-confidence suggestions
- Keep high-confidence matches

#### **2. Add Files Manually** ➕
- Search all 147 project files
- Select additional files to modify
- Specify CREATE vs MODIFY for each

#### **3. Change Task Types** 🔄
- AI suggests MODIFY → change to CREATE
- AI suggests CREATE → change to MODIFY

#### **4. Provide Feedback** 💬
- Add comments about why changes made
- Request specific modifications
- Document reasoning

#### **5. Three Final Actions**
- **Approve & Continue**: Proceed with selected files
- **Request Changes**: Go back and revise ticket/requirements
- **Cancel Workflow**: Stop entirely

---

## 🔧 **CONFIGURATION OPTIONS**

### **Backend Configuration**

```python
# backend_clean.py or config file

APPROVAL_CONFIG = {
    "mode": "confidence_based",  # always, confidence_based, never
    "thresholds": {
        "very_high": 0.90,  # Auto-proceed
        "high": 0.75,       # Auto-proceed with notification
        "medium": 0.50,     # Require approval
        "low": 0.00         # Require approval + manual selection
    },
    "always_approve_phases": [],  # e.g., ["planning"] to skip planning approval
    "require_approval_for": [     # Force approval for these actions
        "database_changes",
        "api_contract_changes",
        "file_deletions"
    ]
}
```

### **Per-Ticket Override**

```json
{
  "ticket_id": "VE-12345",
  "title": "Add email validation",
  "approval_mode": "always",  // Override global setting
  "min_confidence": 0.85      // Override threshold
}
```

### **Project-Level Settings**

```yaml
# .aviator/config.yml
approval:
  mode: confidence_based
  thresholds:
    auto_proceed: 0.80  # Higher threshold for this project
    require_review: 0.60
  
  critical_files:  # Always require approval for these
    - "*/SecurityConfig.java"
    - "*/DatabaseMigration*.sql"
    - "*/AuthenticationService.java"
  
  auto_approve_files:  # Never require approval for these
    - "*/README.md"
    - "*/documentation/**"
```

---

## 📈 **CONFIDENCE CALCULATION**

### **How Confidence is Computed**

```python
def calculate_localization_confidence(
    target_files,
    target_methods,
    dependencies,
    requirements
) -> float:
    score = 0.0
    
    # Factor 1: Found target files (40% weight)
    found_count = len(target_files)
    expected_count = len(requirements.affected_components)
    if found_count > 0:
        score += 0.4 * min(found_count / max(expected_count, 1), 1.0)
    
    # Factor 2: Found target methods (30% weight)
    if len(target_methods) > 0:
        score += 0.3
    
    # Factor 3: Dependency analysis completed (20% weight)
    if len(dependencies) > 0:
        score += 0.2
    
    # Factor 4: All affected components covered (10% weight)
    all_covered = all(
        any(comp.lower() in f.file_path.lower() for f in target_files)
        for comp in requirements.affected_components
    )
    if all_covered:
        score += 0.1
    
    return min(score, 1.0)
```

### **Example Calculations**

**Example 1: High Confidence (0.95)**
```
Requirement: "Modify SupplierService"
Found files: SupplierService.java (exact match) ✅
Found methods: saveSupplier(), findById() ✅
Dependencies: SupplierController, OrderService ✅
All components covered: Yes ✅
→ Score: 0.4 + 0.3 + 0.2 + 0.1 = 1.0 → Confidence: 0.95
```

**Example 2: Medium Confidence (0.62)**
```
Requirement: "Modify SupplierService and DTO"
Found files: SupplierService.java ✅, SupplierDTO.java (ambiguous) ⚠️
Found methods: saveSupplier() ✅
Dependencies: None ❌
All components covered: Partial ⚠️
→ Score: 0.25 + 0.3 + 0.0 + 0.05 = 0.60 → Confidence: 0.62
```

**Example 3: Low Confidence (0.35)**
```
Requirement: "Fix validation bug"
Found files: Guessed ValidationService.java (not in AST) ❌
Found methods: None ❌
Dependencies: None ❌
All components covered: No ❌
→ Score: 0.15 + 0.0 + 0.0 + 0.0 = 0.15 → Confidence: 0.35
```

---

## 🚀 **WORKFLOW WITH APPROVALS**

### **Complete Flow**

```
User submits ticket
         ↓
Investigation Agent
         ↓
Analysis Agent
         ↓
Localization Agent
    ├─ Confidence: 0.94 ✅
    └─ Auto-proceed (no approval)
         ↓
Planning Agent
         ↓
RAG Agents
         ↓
Code Generators
         ↓
Build & Test
         ↓
✅ Complete


VS


User submits ticket
         ↓
Investigation Agent
         ↓
Analysis Agent
         ↓
Localization Agent
    ├─ Confidence: 0.58 ⚠️
    └─ Trigger approval
         ↓
┌───────────────────────┐
│  APPROVAL DIALOG      │
│                       │
│  Files suggested:     │
│  [✓] Service.java     │
│  [✗] DTO.java (remove)│
│                       │
│  Add files:           │
│  [+] Util.java        │
│                       │
│  [Approve]            │
└───────────────────────┘
         ↓
User approves with modifications
    ├─ New confidence: 1.0 ✅ (user approved)
    └─ Final files: Service.java, Util.java
         ↓
Planning Agent
         ↓
RAG Agents
         ↓
Code Generators
         ↓
Build & Test
         ↓
✅ Complete
```

---

## ⚙️ **API ENDPOINTS FOR APPROVALS**

### **1. Get Approval Request**

```http
GET /api/workflow/{workflow_id}/approval
```

**Response**:
```json
{
  "approval_request": {
    "phase": "localization",
    "confidence": 0.62,
    "approval_level": "required",
    "message": "Medium confidence - please review",
    "suggested_files": [
      {
        "file_path": "src/services/SupplierService.java",
        "task_type": "modify",
        "confidence": 0.85,
        "reason": "Exact match in AST"
      }
    ],
    "available_files": [
      "src/services/SupplierService.java",
      "src/services/ValidationService.java",
      "src/utils/EmailValidator.java",
      "..."
    ],
    "impact_analysis": {
      "affected_apis": ["/api/suppliers"],
      "affected_services": ["SupplierService"],
      "requires_migration": false
    }
  }
}
```

### **2. Submit Approval Response**

```http
POST /api/workflow/{workflow_id}/approval
```

**Request Body**:
```json
{
  "action": "approve",
  "approved_files": [
    {
      "file_path": "src/services/SupplierService.java",
      "task_type": "modify"
    }
  ],
  "additional_files": [
    "src/utils/EmailValidator.java"
  ],
  "removed_files": [
    "src/dto/SupplierDTO.java"
  ],
  "file_task_types": {
    "src/utils/EmailValidator.java": "modify"
  },
  "feedback": "Added EmailValidator.java for the regex pattern"
}
```

**Response**:
```json
{
  "status": "approved",
  "workflow_id": "wf-12345",
  "updated_confidence": 1.0,
  "final_files": [
    "src/services/SupplierService.java",
    "src/utils/EmailValidator.java"
  ],
  "message": "Proceeding with user-approved files"
}
```

---

## 💡 **BEST PRACTICES**

### **For Users**

1. **Review Low Confidence** (< 0.75)
   - Always review AI suggestions
   - Check if all necessary files included
   - Add files AI might have missed

2. **Trust High Confidence** (≥ 0.90)
   - Usually safe to auto-proceed
   - Can still manually review if desired

3. **Use Feedback Field**
   - Document why you added/removed files
   - Helps improve AI over time
   - Useful for team communication

4. **Check Impact Analysis**
   - Understand what else is affected
   - Verify API changes are acceptable
   - Ensure migrations are planned

### **For Administrators**

1. **Set Appropriate Mode**
   - Production: `confidence_based` or `always`
   - Development: `confidence_based`
   - Testing: `never` (only if safe)

2. **Configure Thresholds**
   - Financial/Healthcare: Raise to 0.85+
   - General software: Keep at 0.75
   - Internal tools: Lower to 0.65

3. **Define Critical Files**
   - Always require approval for security
   - Always require approval for DB migrations
   - Always require approval for API contracts

4. **Monitor Confidence Trends**
   - Track average confidence over time
   - Identify patterns in low-confidence cases
   - Improve indexing/documentation

---

## 📊 **CONFIDENCE IMPROVEMENT TIPS**

### **To Increase Confidence**

1. **Better Indexing**
   - Ensure SQLite index is up-to-date
   - Run `index_repository()` regularly
   - Keep Neo4j synchronized

2. **Better Documentation**
   - Add comments describing components
   - Document architecture in RAG docs
   - Maintain up-to-date README

3. **Clearer Tickets**
   - Specify exact component names
   - Include file paths in ticket
   - Provide clear acceptance criteria

4. **Historical Data**
   - System learns from approved tickets
   - Similar tickets have higher confidence
   - Patterns emerge over time

---

## 🎯 **SUMMARY**

| Feature | Value |
|---------|-------|
| **Default Threshold** | 0.75 |
| **High Confidence** | ≥ 0.90 (auto-proceed) |
| **Low Confidence** | < 0.50 (manual selection) |
| **Approval Modes** | Always, Confidence-Based, Never |
| **Manual Selection** | ✅ Supported |
| **File Addition** | ✅ Supported |
| **File Removal** | ✅ Supported |
| **Task Type Override** | ✅ Supported |
| **User Feedback** | ✅ Supported |
| **Per-Project Config** | ✅ Supported |

**The system gives you FULL CONTROL while maintaining AI intelligence!** 🚀

---

**END OF DOCUMENT**
