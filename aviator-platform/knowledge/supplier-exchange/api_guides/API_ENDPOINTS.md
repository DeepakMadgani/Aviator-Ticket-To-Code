# REST API Guide - Supplier Exchange (From Codebase)

## API Endpoints by Service

### area-service

#### AreasControllerIT
Package: `com.opentext.solutions.services.area.rest.controllers`

#### ControllerTest
Package: `com.opentext.solutions.services.area.rest.controllers`

#### DeliverableZipControllerTest
Package: `com.opentext.solutions.services.area.rest.controllers`

#### TransmittalControllerTest
Package: `com.opentext.ene.transmittals.controllers`

#### MigratePicklistController
Package: `com.opentext.solutions.taxonomy.rest.controller`

#### AreaController
Package: `com.opentext.solutions.services.area.rest.controllers`

#### DeliverableReportController
Package: `com.opentext.solutions.services.area.rest.controllers`

#### DeliverableZipController
Package: `com.opentext.solutions.services.area.rest.controllers`

#### PackageReportController
Package: `com.opentext.solutions.services.area.rest.controllers`

#### AreaNameMigrationController
Package: `com.opentext.solutions.services.area.migration.rest.controller`

#### BulkUploadTemplateController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### CoversheetController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### DeliverableCollectionController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### DeliverableItemCollectionController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### DeliverableItemController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

**Endpoints:**
- `/download`
- `/{renditionName}/download`
- `/`
- `/legacyItem`

#### LibraryController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### LibraryZipController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### RegistersCollectionController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### RegisterValidationController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### RegisterVersionsController
Package: `com.opentext.solutions.services.area.domain.engineering.rest`

#### TransmittalController
Package: `com.opentext.ene.transmittals.controllers`

### ene-load-service

#### LoadControllerTest
Package: `com.opentext.bim.loadservice.controllers`

#### LoadController
Package: `com.opentext.bim.loadservice.controller`

**Endpoints:**
- `/{projectId}/loads`
- `/{projectId}/loads/{loadId}/download-report`
- `/{projectId}/loads/{loadId}/report`
- `/{projectId}/loads/{loadId}/validation-report`
- `/{projectId}/loads/{loadId}/rows`
- `/{projectId}/loads/{loadType}`
- `/{projectId}/loads/{loadId}/generate-report`
- `/{projectId}/loads/{loadId}/init`
- `/{projectId}/loads/{loadId}/records/{rowIndex}`
- `/{projectId}/loads/{loadId}/deliverable-report`
- `/projects`

#### GenericControllerHandler
Package: `com.opentext.bim.loadservice.controller.exceptionhandler`

### project-service

#### NumberingControllerTest
Package: `com.opentext.solutions.ene.numbering.controller`

#### DataDeletionController
Package: `com.opentext.bim.projectservice.controller`

**Endpoints:**
- `/`

#### PicklistController
Package: `com.opentext.bim.projectservice.controller`

**Endpoints:**
- `/{id}`

#### PicklistMappingController
Package: `com.opentext.bim.projectservice.controller`

**Endpoints:**
- `/{scope}/{scope-id}`

#### PicklistValueController
Package: `com.opentext.bim.projectservice.controller`

**Endpoints:**
- `/picklists/values/update-for-contracts`

#### GenericControllerHandler
Package: `com.opentext.bim.projectservice.controller.exceptionhandler`

#### ClientConfigurationController
Package: `com.opentext.bim.projectservice.config.controller`

### services

#### DeliverableControllerTest
Package: `com.opentext.solutions.services.xchangev2.controller`

#### LibraryControllerTest
Package: `com.opentext.solutions.services.xchangev2.controller`

#### IssueControllerTest
Package: `com.opentext.solutions.services.xchangev2.issue.controllers`

#### UserController
Package: `com.opentext.solutions.services.xchangev2.user.controllers`

#### TransmittalController
Package: `com.opentext.solutions.services.xchangev2.transmittal.controllers`

#### PicklistController
Package: `com.opentext.solutions.services.xchangev2.taxonomy.controllers`

#### ServiceController
Package: `com.opentext.solutions.services.xchangev2.service.controller`

#### RoleMemberController
Package: `com.opentext.solutions.services.xchangev2.rolemembers.controllers`

#### RegisterController
Package: `com.opentext.solutions.services.xchangev2.register.controllers`

#### ProjectController
Package: `com.opentext.solutions.services.xchangev2.project.controllers`

#### PackageController
Package: `com.opentext.solutions.services.xchangev2.packages.controllers`

#### NotificationController
Package: `com.opentext.solutions.services.xchangev2.notification.controllers`

#### AreaNameMigrationController
Package: `com.opentext.solutions.services.xchangev2.migration.controllers`

#### MigrationController
Package: `com.opentext.solutions.services.xchangev2.migration.controllers`

**Endpoints:**
- `/connector/migrate-deleted-items`

#### LibraryController
Package: `com.opentext.solutions.services.xchangev2.library.controller`

#### IssueController
Package: `com.opentext.solutions.services.xchangev2.issue.controllers`

#### DeliverableSetController
Package: `com.opentext.solutions.services.xchangev2.deliverableset.controllers`

#### DeliverableListController
Package: `com.opentext.solutions.services.xchangev2.deliverablelist.controllers`

#### DeliverableController
Package: `com.opentext.solutions.services.xchangev2.deliverable.controllers`

#### CoversheetController
Package: `com.opentext.solutions.services.xchangev2.coversheet.controllers`

#### ContractController
Package: `com.opentext.solutions.services.xchangev2.contract.controllers`



## Common API Patterns

### Response Format
```json
{
  "data": {...},
  "status": "success",
  "message": "Operation completed"
}
```

### Error Response
```json
{
  "error": "VALIDATION_ERROR",
  "message": "Invalid input",
  "details": [...]
}
```

### Pagination
```
GET /api/v1/resources?page=0&size=20&sort=createdAt,desc
```

## Authentication
- OAuth 2.0 via OpenText Directory Services (OTDS)
- Bearer token in Authorization header
- Token expiration: 1 hour

## AI Agent API Usage

When making API calls:
1. **ALWAYS** include Authorization header
2. **VALIDATE** response status codes
3. **HANDLE** pagination for large result sets
4. **RETRY** with backoff on 5xx errors
5. **LOG** all API interactions
