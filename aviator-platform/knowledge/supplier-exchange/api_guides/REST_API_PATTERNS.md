# REST API Patterns - Supplier Exchange

## Authentication

### OpenText Directory Services (OTDS)
```
POST /otdsauth/login
Headers:
  Content-Type: application/json
Body:
  {
    "username": "user@example.com",
    "password": "..."
  }

Response:
  {
    "access_token": "...",
    "refresh_token": "...",
    "expires_in": 3600
  }
```

### Using Access Token
```
All API requests:
Headers:
  Authorization: Bearer {access_token}
```

## Project API Endpoints

### List Projects
```
GET /api/v1/projects
Query Params:
  - page (default: 0)
  - size (default: 20)
  - status (optional filter)
  - sortBy (optional)

Response: 200 OK
{
  "content": [...],
  "totalElements": 150,
  "totalPages": 8
}
```

### Create Project
```
POST /api/v1/projects
Body:
{
  "name": "Project Name",
  "description": "...",
  "contractId": "contract-123",
  "startDate": "2026-01-01"
}

Response: 201 Created
Location: /api/v1/projects/{id}
```

## Task API Endpoints

### Create Task
```
POST /api/v1/projects/{projectId}/tasks
Body:
{
  "title": "Task Title",
  "description": "...",
  "assigneeId": "user-123",
  "dueDate": "2026-06-01",
  "priority": "HIGH"
}
```

### Update Task Status
```
PATCH /api/v1/tasks/{taskId}/status
Body:
{
  "status": "IN_PROGRESS",
  "comment": "Starting work"
}
```

## Common Error Responses

### 400 Bad Request
```json
{
  "error": "VALIDATION_ERROR",
  "message": "Invalid input",
  "details": [
    {
      "field": "email",
      "message": "Invalid email format"
    }
  ]
}
```

### 403 Forbidden
```json
{
  "error": "PERMISSION_DENIED",
  "message": "User does not have permission to perform this action"
}
```

### 404 Not Found
```json
{
  "error": "RESOURCE_NOT_FOUND",
  "message": "Project with id 'abc-123' not found"
}
```

## AI Agent API Usage Guidelines

### Best Practices
1. **ALWAYS** include proper Authorization header
2. **VALIDATE** responses (check status codes)
3. **HANDLE** rate limiting (429 responses)
4. **RETRY** with exponential backoff on 5xx errors
5. **LOG** all API calls for debugging

### Error Handling Pattern
```java
try {
    Response response = apiClient.post(url, body);
    
    if (response.getStatus() == 201) {
        return response.getBody();
    } else if (response.getStatus() == 400) {
        throw new ValidationException(response.getError());
    } else if (response.getStatus() == 403) {
        throw new PermissionDeniedException();
    } else if (response.getStatus() >= 500) {
        // Retry logic
        return retryWithBackoff(url, body);
    }
} catch (IOException e) {
    logger.error("API call failed", e);
    throw new ExternalSystemException(e);
}
```

## Rate Limiting
- Default: 100 requests per minute per user
- Burst: 10 requests per second
- Response Header: `X-RateLimit-Remaining`

## Pagination Standard
```
GET /api/v1/resources?page=0&size=20

Response includes:
{
  "content": [...],
  "pageable": {
    "pageNumber": 0,
    "pageSize": 20
  },
  "totalElements": 150,
  "totalPages": 8,
  "first": true,
  "last": false
}
```
