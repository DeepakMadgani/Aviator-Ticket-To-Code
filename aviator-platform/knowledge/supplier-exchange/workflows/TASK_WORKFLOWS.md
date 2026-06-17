# Task Management Workflows

## Task Creation Workflow

```
1. Validate Project Exists
   ↓
2. Check User Permissions (Can Create Tasks)
   ↓
3. Validate Required Fields
   ↓
4. Assign Task Number/ID
   ↓
5. Set Initial Status (Draft/New)
   ↓
6. Save to Database
   ↓
7. Trigger Notifications
```

### Code Path
```
TaskController.createTask()
  → TaskService.validateAndCreate()
    → TaskRepository.save()
    → NotificationService.notifyAssignees()
```

## Task Assignment Workflow

```
1. Check Current Task Status
   ↓
2. Validate Assignee Permissions
   ↓
3. Check Assignee Availability
   ↓
4. Update Assignment
   ↓
5. Send Notification
   ↓
6. Update Task Status (if needed)
```

## Task Status Transitions

### Valid State Machine
```
New → Assigned → In Progress → Review → Completed
       ↓           ↓            ↓
       Cancelled   On Hold      Rejected → In Progress
```

### Transition Rules
- **New → Assigned**: Requires valid assignee
- **Assigned → In Progress**: Only assignee can start
- **In Progress → Review**: Must have deliverables attached
- **Review → Completed**: Requires approver action
- **Any → Cancelled**: Requires admin or project manager role

## Task Dependencies

### Predecessor/Successor Logic
```java
// Task B cannot start until Task A is completed
if (predecessorTask.getStatus() != TaskStatus.COMPLETED) {
    throw new ValidationException("Predecessor task must be completed first");
}
```

## AI Agent Task Guidelines

When modifying tasks:
1. **ALWAYS** check current status before updates
2. **NEVER** skip validation steps
3. **ALWAYS** use the service layer (not direct repository access)
4. **VALIDATE** all state transitions are legal
5. **MAINTAIN** audit trail of all changes

### Example Task Modification Pattern
```java
@Transactional
public Task updateTaskStatus(Long taskId, TaskStatus newStatus) {
    Task task = taskRepository.findById(taskId)
        .orElseThrow(() -> new ResourceNotFoundException("Task not found"));
    
    // Validate transition
    if (!isValidTransition(task.getStatus(), newStatus)) {
        throw new ValidationException("Invalid status transition");
    }
    
    // Check permissions
    if (!hasPermission(currentUser, task, "UPDATE_STATUS")) {
        throw new PermissionDeniedException();
    }
    
    task.setStatus(newStatus);
    task.setLastModified(LocalDateTime.now());
    
    Task updated = taskRepository.save(task);
    
    // Trigger side effects
    notificationService.notifyStatusChange(updated);
    auditService.logStatusChange(task.getId(), oldStatus, newStatus);
    
    return updated;
}
```
