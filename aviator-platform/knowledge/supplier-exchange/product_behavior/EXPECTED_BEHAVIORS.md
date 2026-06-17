# Product Behavior - Supplier Exchange (Core Collaboration for Engineering)

## System Behavior Patterns

### Project Lifecycle Behavior

#### Project Creation
- **Expected Behavior**: When creating a project, system validates all mandatory fields before saving
- **Side Effects**: 
  - Creates default folder structure in Library
  - Initializes default permissions based on template
  - Sends notifications to project administrators
- **Error Scenarios**: 
  - Duplicate project name: Returns validation error
  - Invalid contract reference: Returns 400 error
  - Insufficient permissions: Returns 403 error

#### Project Activation
- **Trigger**: Status change from Draft to Active
- **Prerequisites**: Must have assigned contract, valid dates, at least one member
- **Behavior**: Enables all project features, locks certain configuration fields

### Task Behavior

#### Task Status Changes
- **In Progress Behavior**: 
  - Locks reassignment (only assignee or admin can reassign)
  - Starts time tracking (if enabled)
  - Enables deliverable attachment
- **Completion Behavior**:
  - Validates all deliverables are approved
  - Triggers successor task notifications
  - Updates project progress percentage

### Deliverable Behavior

#### Deliverable Submission
- **Expected Flow**: User uploads file → System scans for viruses → Creates version → Triggers review workflow
- **Versioning**: System auto-increments version numbers (1.0, 1.1, 2.0, etc.)
- **Notifications**: Automatically notifies reviewers when new version uploaded

### Transmittal Behavior

#### Transmittal Sending
- **Behavior**: 
  - Creates snapshot of deliverable at current version
  - Generates PDF summary document
  - Sends email notifications to recipients
  - Creates audit trail entry
- **Status Tracking**: Tracks read receipts, acknowledgments, comments

## Common User Scenarios

### Scenario 1: Creating Task with Dependencies
1. User creates predecessor task (Task A)
2. User creates successor task (Task B) with Task A as predecessor
3. System prevents Task B from starting until Task A is completed
4. When Task A completes, system notifies Task B assignee

### Scenario 2: Deliverable Review Cycle
1. Engineer uploads deliverable
2. System notifies reviewer
3. Reviewer rejects with comments
4. Engineer uploads revised version
5. System creates new version (e.g., 1.0 → 1.1)
6. Reviewer approves
7. Status changes to Approved

## Known Limitations

- **Concurrent Editing**: Last write wins (no merge conflict resolution)
- **File Size Limits**: Maximum 100MB per file upload
- **Batch Operations**: Maximum 50 items per batch API call
- **Search Limitations**: Full-text search may have 1-2 minute indexing delay

## Performance Characteristics

- **Project Creation**: Typically completes in 2-5 seconds
- **Large Reports**: May take 30-60 seconds for >10,000 records
- **File Upload**: Network dependent, progress indicator shows status
- **Search**: Returns results in <2 seconds for most queries

## AI Agent Behavioral Guidelines

When modifying system entities:
1. **Respect state machine rules** - Don't skip intermediate states
2. **Trigger side effects** - Don't just update database, call service methods
3. **Check cascading impacts** - Understand what else gets affected
4. **Validate assumptions** - Don't assume data exists, always check

### Example: Safe Task Update
```java
// WRONG - Direct database update
task.setStatus(TaskStatus.COMPLETED);
taskRepository.save(task);

// RIGHT - Use service layer
taskService.completeTask(taskId, completionNotes);
// This triggers: validation, notifications, successor tasks, audit logging
```
