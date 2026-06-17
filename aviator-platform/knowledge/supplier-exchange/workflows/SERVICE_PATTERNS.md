# Common Workflows - Supplier Exchange

## Service Layer Execution Flow

### Typical Request Flow
```
HTTP Request
    ↓
Controller (Validation + DTO conversion)
    ↓
Service (Business logic + Transaction)
    ↓
Repository (Data access)
    ↓
Database
```

### Transaction Boundaries
- **Start**: Service layer method entry
- **Commit**: Successful completion
- **Rollback**: Exception thrown

## Error Handling Workflow

### Controller-Level
```java
@ExceptionHandler(ValidationException.class)
public ResponseEntity<ErrorResponse> handleValidation(ValidationException e) {
    return ResponseEntity
        .badRequest()
        .body(new ErrorResponse(e.getMessage()));
}
```

### Service-Level
```java
@Transactional
public Entity updateEntity(Long id, EntityDTO dto) {
    Entity entity = repository.findById(id)
        .orElseThrow(() -> new NotFoundException("Entity not found"));
    
    // Business validation
    if (!isValid(entity, dto)) {
        throw new BusinessRuleException("Invalid update");
    }
    
    // Update
    entity.update(dto);
    return repository.save(entity);
}
```

## AI Agent Workflow Guidelines

When implementing changes:
1. **START** at Controller layer
2. **VALIDATE** at Controller
3. **EXECUTE** business logic in Service
4. **PERSIST** via Repository
5. **HANDLE** exceptions at each layer
