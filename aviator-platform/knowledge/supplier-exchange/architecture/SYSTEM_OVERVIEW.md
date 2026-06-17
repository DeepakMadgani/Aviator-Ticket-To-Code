# Supplier Exchange - System Architecture Overview

## Product Information
- **Product**: OpenText Core Collaboration for Engineering
- **Version**: 26.2
- **Last Updated**: March 2026
- **System Type**: Engineering Collaboration Platform

## Core Components

### 1. Projects
- Project management and organization
- Project hierarchy and relationships
- Project lifecycle management

### 2. Contracts
- Contract management
- Contract workflows
- Contract relationships to projects

### 3. Tasks
- Task creation and assignment
- Task workflows
- Task dependencies and tracking

### 4. Deliverables
- Deliverable management
- Deliverable tracking
- Deliverable approval workflows

### 5. Transmittals
- Document transmittal process
- Transmittal workflows
- Transmittal tracking and acknowledgment

### 6. Reports
- Reporting capabilities
- Report types and generation
- Report customization

### 7. Library (Files and Folders)
- Document management
- File organization
- Version control
- Access control

### 8. Registers
- Register management
- Register types
- Register workflows

### 9. Members
- User management
- Role assignments
- Permission management

## Key Architecture Principles

### Roles and Permissions
- Role-based access control (RBAC)
- Permission inheritance
- Custom role configuration

### Integration Points
- REST APIs for external systems
- Authentication via OTDS (OpenText Directory Services)
- Cloud-based deployment on OpenText Cloud Platform

## AI Agent Guidelines

When working with this system:
- **ALWAYS** respect role-based permissions
- **NEVER** bypass security controls
- **ALWAYS** maintain audit trails for modifications
- **NEVER** directly modify database records (use APIs)

## Related Documentation
- Introduction and roles: See roles_and_permissions.md
- API Integration: See api_integration.md
- Workflows: See workflow_patterns.md
