# Common Issues and Solutions - Supplier Exchange

## Authentication Issues

### Issue: Login Fails with 401 Unauthorized
**Symptoms**: API calls return 401, OTDS authentication fails
**Common Causes**:
- Token expired (tokens valid for 1 hour)
- Invalid credentials
- Account locked after multiple failed attempts
**Solution**:
```java
// Implement token refresh logic
if (response.getStatus() == 401) {
    // Refresh token
    String newToken = authService.refreshAccessToken(refreshToken);
    // Retry request with new token
    return retryRequest(newToken);
}
```

### Issue: Permission Denied (403)
**Symptoms**: User has valid token but cannot perform action
**Common Causes**:
- Insufficient role permissions
- User not member of project
- Entity in locked state
**Debugging Steps**:
1. Check user's assigned roles: `GET /api/v1/users/{userId}/roles`
2. Verify project membership: `GET /api/v1/projects/{projectId}/members`
3. Check entity status (locked entities cannot be modified)

## Task Management Issues

### Issue: Cannot Start Task (Status Transition Fails)
**Symptoms**: Task status change rejected with validation error
**Common Causes**:
- Predecessor task not completed
- Task not assigned
- User not the assignee
**Solution Pattern**:
```java
// Check prerequisites before attempting status change
Task task = taskService.getTask(taskId);
if (task.hasPredecessors()) {
    List<Task> predecessors = taskService.getPredecessorTasks(taskId);
    if (!allCompleted(predecessors)) {
        throw new ValidationException("Complete predecessor tasks first");
    }
}
```

### Issue: Task Stuck in "In Progress" State
**Symptoms**: Cannot complete task even with all deliverables submitted
**Common Causes**:
- Required deliverables missing approval
- Mandatory checklist items incomplete
- Workflow requires specific reviewer role not assigned
**Resolution**:
1. Check deliverable statuses
2. Verify all approval requirements met
3. Ensure no blocking validation rules

## Deliverable Upload Issues

### Issue: File Upload Fails
**Symptoms**: 413 Payload Too Large or timeout
**Common Causes**:
- File exceeds size limit (100MB)
- Network timeout
- Invalid file type
**Solutions**:
- For large files: Use chunked upload API
- Enable upload resume capability
- Compress files before upload

### Issue: Version Number Not Incrementing
**Symptoms**: New upload doesn't create new version
**Cause**: Uploaded as separate deliverable instead of new version
**Correct Process**:
```
PUT /api/v1/deliverables/{deliverableId}/versions
(not POST /api/v1/deliverables)
```

## Report Generation Issues

### Issue: Report Takes Too Long to Generate
**Symptoms**: Report request times out or takes >2 minutes
**Common Causes**:
- Too broad date range (>1 year)
- Querying too many entities (>10,000 records)
- Complex filters with multiple joins
**Optimization**:
- Narrow date range
- Filter by specific projects/contracts
- Use pagination for large result sets
- Generate reports asynchronously

### Issue: Missing Data in Reports
**Symptoms**: Expected records not appearing in report
**Common Causes**:
- Permission-based filtering (user can't see all data)
- Incorrect date range or filters
- Data still being indexed (search lag)
**Check**:
1. Verify user has access to relevant projects
2. Double-check filter criteria
3. Wait 2-3 minutes for search index to update

## Integration Issues

### Issue: API Rate Limit Exceeded (429 Response)
**Symptoms**: Requests start failing with 429 Too Many Requests
**Solution**:
```java
// Implement exponential backoff
private Response callWithBackoff(Request request, int maxRetries) {
    int retries = 0;
    while (retries < maxRetries) {
        Response response = httpClient.execute(request);
        
        if (response.getStatus() == 429) {
            int retryAfter = response.getHeader("Retry-After", 60);
            Thread.sleep(retryAfter * 1000 * Math.pow(2, retries));
            retries++;
        } else {
            return response;
        }
    }
    throw new RateLimitException();
}
```

### Issue: Webhook Not Triggering
**Symptoms**: No notifications received for events
**Common Causes**:
- Webhook endpoint unreachable
- SSL certificate validation failing
- Incorrect event subscription
**Debugging**:
1. Check webhook configuration: `GET /api/v1/webhooks/{id}`
2. Verify endpoint is publicly accessible
3. Check webhook delivery logs
4. Test with webhook.site for debugging

## Data Consistency Issues

### Issue: Stale Data Displayed in UI
**Symptoms**: UI shows old data even after update
**Common Causes**:
- Browser caching
- API response caching
- Search index lag
**Solutions**:
- Force cache refresh with `Cache-Control: no-cache` header
- Use ETags for conditional requests
- Poll for updates after modifications

## AI Agent Troubleshooting Guidelines

When encountering errors:

1. **Read the error response body** - Contains specific error code and details
2. **Check status code pattern**:
   - 4xx = Client error (fix your request)
   - 5xx = Server error (retry or escalate)
3. **Log full request/response** for debugging
4. **Never retry 4xx errors** without fixing the issue
5. **Always implement circuit breaker** for external calls

### Example Error Handler
```java
@ExceptionHandler(ApiException.class)
public ResponseEntity<ErrorResponse> handleApiError(ApiException e) {
    logger.error("API call failed: {} - {}", e.getStatusCode(), e.getMessage());
    
    // Don't retry on client errors
    if (e.getStatusCode() >= 400 && e.getStatusCode() < 500) {
        return ResponseEntity
            .status(e.getStatusCode())
            .body(new ErrorResponse(e.getMessage()));
    }
    
    // Implement retry logic for server errors
    return handleServerError(e);
}
```

## Escalation Paths

- **Authentication Issues**: Check OTDS configuration
- **Permission Issues**: Contact project administrator
- **Data Issues**: Contact support with entity ID and timestamp
- **Performance Issues**: Check system status page, contact ops team
