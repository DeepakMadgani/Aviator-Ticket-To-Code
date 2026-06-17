# Supplier Exchange - System Architecture (Auto-Generated)

## Microservices Architecture

### Identified Microservices (10)

#### area-service
- **Type**: Microservice
- **Location**: `area-service/`
- **API Controllers**: 21
  - `AreasControllerIT`
  - `ControllerTest`
  - `DeliverableZipControllerTest`
  - `TransmittalControllerTest`
  - `MigratePicklistController`

#### bim-commons-lib
- **Type**: Microservice
- **Location**: `bim-commons-lib/`

#### bim-events-lib
- **Type**: Microservice
- **Location**: `bim-events-lib/`

#### bim-gateway
- **Type**: Microservice
- **Location**: `bim-gateway/`

#### ene-load-service
- **Type**: Microservice
- **Location**: `ene-load-service/`
- **API Controllers**: 3
  - `LoadControllerTest`
  - `LoadController`
  - `GenericControllerHandler`

#### project-service
- **Type**: Microservice
- **Location**: `project-service/`
- **API Controllers**: 7
  - `NumberingControllerTest`
  - `DataDeletionController`
  - `PicklistController`
  - `PicklistMappingController`
  - `PicklistValueController`

#### sagas-lib
- **Type**: Microservice
- **Location**: `sagas-lib/`

#### sagas-service
- **Type**: Microservice
- **Location**: `sagas-service/`

#### se-connector-apis
- **Type**: Microservice
- **Location**: `se-connector-apis/`
- **API Controllers**: 21
  - `DeliverableControllerTest`
  - `LibraryControllerTest`
  - `IssueControllerTest`
  - `UserController`
  - `TransmittalController`

#### xchange-ui
- **Type**: Microservice
- **Location**: `xchange-ui/`


## Component Breakdown

### Controllers (52)
REST API controllers handling HTTP requests:
- `DeliverableControllerTest` - Package: `com.opentext.solutions.services.xchangev2.controller`
- `LibraryControllerTest` - Package: `com.opentext.solutions.services.xchangev2.controller`
- `IssueControllerTest` - Package: `com.opentext.solutions.services.xchangev2.issue.controllers`
- `UserController` - Package: `com.opentext.solutions.services.xchangev2.user.controllers`
- `TransmittalController` - Package: `com.opentext.solutions.services.xchangev2.transmittal.controllers`
- `PicklistController` - Package: `com.opentext.solutions.services.xchangev2.taxonomy.controllers`
- `ServiceController` - Package: `com.opentext.solutions.services.xchangev2.service.controller`
- `RoleMemberController` - Package: `com.opentext.solutions.services.xchangev2.rolemembers.controllers`
- `RegisterController` - Package: `com.opentext.solutions.services.xchangev2.register.controllers`
- `ProjectController` - Package: `com.opentext.solutions.services.xchangev2.project.controllers`
- `PackageController` - Package: `com.opentext.solutions.services.xchangev2.packages.controllers`
- `NotificationController` - Package: `com.opentext.solutions.services.xchangev2.notification.controllers`
- `AreaNameMigrationController` - Package: `com.opentext.solutions.services.xchangev2.migration.controllers`
- `MigrationController` - Package: `com.opentext.solutions.services.xchangev2.migration.controllers`
- `LibraryController` - Package: `com.opentext.solutions.services.xchangev2.library.controller`
- `IssueController` - Package: `com.opentext.solutions.services.xchangev2.issue.controllers`
- `DeliverableSetController` - Package: `com.opentext.solutions.services.xchangev2.deliverableset.controllers`
- `DeliverableListController` - Package: `com.opentext.solutions.services.xchangev2.deliverablelist.controllers`
- `DeliverableController` - Package: `com.opentext.solutions.services.xchangev2.deliverable.controllers`
- `CoversheetController` - Package: `com.opentext.solutions.services.xchangev2.coversheet.controllers`

### Services (179)
Business logic layer:
- `IssueServiceConnectorTest` - Package: `com.opentext.solutions.services.xchangev2.issue.service`
- `DeliverableServiceConnectorTest` - Package: `com.opentext.solutions.services.xchange.service`
- `XchangeConnectorServiceApplication` - Package: `com.opentext.solutions.services`
- `PicklistServiceConnector` - Package: `com.opentext.solutions.services.xchangev2.taxonomy.service`
- `ServiceTransformer` - Package: `com.opentext.solutions.services.xchangev2.service.transformers`
- `AreaNameMigrationServiceConnector` - Package: `com.opentext.solutions.services.xchangev2.migration.service`
- `MigrationServiceConnector` - Package: `com.opentext.solutions.services.xchangev2.migration.service`
- `IssueServiceConnector` - Package: `com.opentext.solutions.services.xchangev2.issue.service`
- `AreaServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `ContractServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `CoversheetServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `DeliverableListServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `DeliverableServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `LibraryServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `MemberServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `NotificationServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `PackageServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `ProjectServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `RegisterServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`
- `TransmittalServiceConnector` - Package: `com.opentext.solutions.services.xchange.service`

### Repositories (26)
Data access layer:
- `CommandRepository` - Package: `com.opentext.bim.sagas.repository`
- `ContractMemberRepository` - Package: `com.opentext.bim.projectservice.repository`
- `ContractRepository` - Package: `com.opentext.bim.projectservice.repository`
- `DeletedDataRepository` - Package: `com.opentext.bim.projectservice.repository`
- `DropRepository` - Package: `com.opentext.bim.projectservice.repository`
- `ParticipatingCompaniesRepository` - Package: `com.opentext.bim.projectservice.repository`
- `ParticipatingMembersRepository` - Package: `com.opentext.bim.projectservice.repository`
- `PicklistMappingRepository` - Package: `com.opentext.bim.projectservice.repository`
- `PicklistRepository` - Package: `com.opentext.bim.projectservice.repository`
- `PicklistValueRepository` - Package: `com.opentext.bim.projectservice.repository`
- `ProjectRepository` - Package: `com.opentext.bim.projectservice.repository`
- `RoleAtStageRepository` - Package: `com.opentext.bim.projectservice.repository`
- `RoleRepository` - Package: `com.opentext.bim.projectservice.repository`
- `SettingsContentRepository` - Package: `com.opentext.bim.projectservice.repository`
- `SettingsRepository` - Package: `com.opentext.bim.projectservice.repository`
- `StageRepository` - Package: `com.opentext.bim.projectservice.repository`
- `UserPreferencesRepository` - Package: `com.opentext.bim.projectservice.repository`
- `R_ContractMemberRepository` - Package: `com.opentext.bim.commons.repository`
- `R_ContractRepository` - Package: `com.opentext.bim.commons.repository`
- `R_ParticipatingMemberRepository` - Package: `com.opentext.bim.commons.repository`


## Architecture Patterns

### Layered Architecture
```
Controller Layer (REST APIs)
    ↓
Service Layer (Business Logic)
    ↓
Repository Layer (Data Access)
    ↓
Database (PostgreSQL)
```

### Microservices Communication
- Synchronous: REST APIs
- Event-driven: Saga pattern for distributed transactions

## Critical AI Agent Rules

⚠️ **NEVER**:
- Skip service layer and call repositories directly from controllers
- Modify entities without proper validation
- Break transactional boundaries
- Bypass authentication/authorization

✅ **ALWAYS**:
- Use service layer for all business logic
- Maintain proper exception handling
- Follow REST API conventions
- Log important operations
