# Core Business Rules - Supplier Exchange

## Project Management Rules

### Project Creation
- **Rule**: [Add your rule - e.g., "Projects must have a valid contract before activation"]
- **Validation**: [How it's validated]
- **Impact**: [What happens if violated]

### Project Hierarchy
- **Rule**: [Add your rule]
- **Enforcement**: [How enforced in code]

## Contract Management Rules

### Contract Assignment
- **Rule**: [Your specific rule]
- **Related Code**: [Java classes that enforce this]

### Contract Workflow
- **States**: [Draft, Active, Completed, etc.]
- **Transitions**: [Valid state transitions]
- **Validation**: [Required fields per state]

## Task Management Rules

### Task Creation
- **Prerequisites**: [What must exist before task creation]
- **Mandatory Fields**: [Required fields]
- **Business Logic**: [Special rules]

### Task Assignment
- **Role Requirements**: [Who can assign tasks]
- **Validation Logic**: [Assignment rules]

## Deliverable Rules

### Deliverable Submission
- **Required Approvals**: [Approval workflow]
- **Status Transitions**: [Valid state changes]
- **Validation**: [What gets checked]

## Data Validation Rules

### Field Validation
```java
// Example validation patterns
// Email format
// Date ranges
// Mandatory fields per entity type
```

### Cross-Entity Validation
- Tasks must belong to valid projects
- Deliverables must be linked to tasks
- Transmittals must reference deliverables

## AI Agent Critical Rules

⚠️ **NEVER**:
- Delete contracts that have active tasks
- Modify completed deliverables
- Change task assignments without approval workflow
- Bypass role-based permissions

✅ **ALWAYS**:
- Validate parent-child relationships
- Check permission before operations
- Maintain audit trail
- Follow state machine transitions
