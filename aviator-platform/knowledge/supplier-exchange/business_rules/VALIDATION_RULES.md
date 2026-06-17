# Business Rules - Supplier Exchange (From Codebase Analysis)

## Validation Rules

### Controller-Level Validation
- All API requests validated using `@Valid` annotation
- Input DTOs must pass Bean Validation constraints
- Path variables and query parameters validated

### Service-Level Business Rules
- Transactional boundaries enforced with `@Transactional`
- Business rule violations throw custom exceptions
- State transitions validated before entity updates

### Common Validation Patterns Found

#### Custom Validators:
- `ContractValidatorTest` - Package: `com.opentext.bim.projectservice.validators`
- `SettingsValidatorTest` - Package: `com.opentext.bim.projectservice.validators`
- `ContractValidator` - Package: `com.opentext.bim.projectservice.validators`
- `MigrationValidator` - Package: `com.opentext.bim.projectservice.validators`
- `OrgAndProjectUserValidator` - Package: `com.opentext.bim.projectservice.validators`
- `OrganizationValidator` - Package: `com.opentext.bim.projectservice.validators`
- `ProjectValidator` - Package: `com.opentext.bim.projectservice.validators`
- `SettingsValidator` - Package: `com.opentext.bim.projectservice.validators`
- `StageValidator` - Package: `com.opentext.bim.projectservice.validators`
- `UserValidator` - Package: `com.opentext.bim.projectservice.validators`


## Entity Relationships

### Identified Entities
- `Contract` - Package: `com.opentext.bim.projectservice.model`
- `ContractMember` - Package: `com.opentext.bim.projectservice.model`
- `DeletedData` - Package: `com.opentext.bim.projectservice.model`
- `Drop` - Package: `com.opentext.bim.projectservice.model`
- `ParticipatingCompany` - Package: `com.opentext.bim.projectservice.model`
- `ParticipatingMember` - Package: `com.opentext.bim.projectservice.model`
- `Picklist` - Package: `com.opentext.bim.projectservice.model`
- `PicklistMapping` - Package: `com.opentext.bim.projectservice.model`
- `PicklistValue` - Package: `com.opentext.bim.projectservice.model`
- `Project` - Package: `com.opentext.bim.projectservice.model`
- `Role` - Package: `com.opentext.bim.projectservice.model`
- `RoleAtStage` - Package: `com.opentext.bim.projectservice.model`
- `Settings` - Package: `com.opentext.bim.projectservice.model`
- `SettingsContent` - Package: `com.opentext.bim.projectservice.model`
- `Stage` - Package: `com.opentext.bim.projectservice.model`


## Transaction Management

### Service Layer Patterns
- Default: `@Transactional(readOnly = true)` for read operations
- Write operations: `@Transactional` (read-write)
- Exception-driven rollback on unchecked exceptions

## AI Agent Guidelines

When modifying code:
1. **ALWAYS** validate inputs at controller layer
2. **NEVER** bypass service layer validation
3. **ALWAYS** use transactions for write operations
4. **VALIDATE** entity state before updates
5. **MAINTAIN** audit trails for critical operations
