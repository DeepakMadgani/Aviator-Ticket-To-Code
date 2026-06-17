# Troubleshooting Guide - Supplier Exchange

## Common Issues by Layer

### Controller Layer Issues

#### 400 Bad Request
**Cause**: Validation failure
**Solution**: Check `@Valid` constraints on DTOs
```java
// Ensure DTO has proper validation annotations
@NotNull
@Size(min = 1, max = 255)
private String name;
```

#### 404 Not Found
**Cause**: Resource not found in database
**Solution**: Verify ID exists, check repository query

### Service Layer Issues

#### TransactionException
**Cause**: Database constraint violation
**Solution**: Check entity relationships, ensure required fields present

#### OptimisticLockException
**Cause**: Concurrent modification
**Solution**: Implement versioning, retry with latest data

### Repository Layer Issues

#### DataIntegrityViolationException
**Cause**: Foreign key constraint violation
**Solution**: Ensure parent entities exist before creating children

## Performance Issues

### Slow Queries
**Cause**: Missing indexes, N+1 queries
**Solution**: 
- Add database indexes
- Use `@EntityGraph` or JOIN FETCH
- Enable query logging to identify slow queries

### Memory Issues
**Cause**: Loading too much data at once
**Solution**:
- Use pagination
- Stream large result sets
- Limit eager loading

## AI Agent Debugging

When encountering errors:
1. **READ** stack trace carefully
2. **CHECK** validation constraints
3. **VERIFY** database state
4. **LOG** inputs and outputs
5. **TEST** in isolation
