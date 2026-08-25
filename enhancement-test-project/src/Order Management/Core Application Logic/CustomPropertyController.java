package com.example.ordermanagement.coreapplicationlogic;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.UUID;

/**
 * Request DTO for creating or updating a custom property definition.
 */
class CustomPropertyDefinitionRequest {
    private String name;
    private String type;
    private String description;
    private String validationPattern; // New field for validation regex pattern

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getType() { return type; }
    public void setType(String type) { this.type = type; }
    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
    public String getValidationPattern() { return validationPattern; }
    public void setValidationPattern(String validationPattern) { this.validationPattern = validationPattern; }
}

/**
 * Response DTO for a custom property definition.
 */
class CustomPropertyDefinitionResponse {
    private String id;
    private String name;
    private String type;
    private String description;
    private String validationPattern; // New field for validation regex pattern

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getType() { return type; }
    public void setType(String type) { this.type = type; }
    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }
    public String getValidationPattern() { return validationPattern; }
    public void setValidationPattern(String validationPattern) { this.validationPattern = validationPattern; }
}

/**
 * Interface for the service layer handling custom property definitions.
 * This is a placeholder for the actual service dependency.
 */
interface PropertyDefinitionsService {
    CustomPropertyDefinitionResponse createDefinition(CustomPropertyDefinitionRequest request);
    CustomPropertyDefinitionResponse updateDefinition(String id, CustomPropertyDefinitionRequest request);
    CustomPropertyDefinitionResponse getDefinition(String id);
}

/**
 * REST Controller for managing custom property definitions.
 * Exposes API endpoints for creating, updating, and retrieving custom property definitions,
 * including a new validation pattern field.
 */
@RestController
@RequestMapping("/api/custom-properties")
public class CustomPropertyController {

    private final PropertyDefinitionsService propertyDefinitionsService;

    /**
     * Constructs a new CustomPropertyController with the given PropertyDefinitionsService.
     *
     * @param propertyDefinitionsService The service responsible for managing custom property definitions.
     */
    public CustomPropertyController(PropertyDefinitionsService propertyDefinitionsService) {
        this.propertyDefinitionsService = propertyDefinitionsService;
    }

    /**
     * Creates a new custom property definition.
     * The request body should include the name, type, description, and an optional validation pattern.
     *
     * @param request The request body containing the custom property definition details.
     * @return A ResponseEntity containing the created custom property definition and HTTP status 201 (Created).
     */
    @PostMapping
    public ResponseEntity<CustomPropertyDefinitionResponse> createCustomPropertyDefinition(
            @RequestBody CustomPropertyDefinitionRequest request) {
        CustomPropertyDefinitionResponse response = propertyDefinitionsService.createDefinition(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(response);
    }

    /**
     * Updates an existing custom property definition.
     * The request body should include the updated name, type, description, and an optional validation pattern.
     *
     * @param id The unique identifier of the custom property definition to update.
     * @param request The request body containing the updated custom property definition details.
     * @return A ResponseEntity containing the updated custom property definition and HTTP status 200 (OK).
     */
    @PutMapping("/{id}")
    public ResponseEntity<CustomPropertyDefinitionResponse> updateCustomPropertyDefinition(
            @PathVariable String id,
            @RequestBody CustomPropertyDefinitionRequest request) {
        CustomPropertyDefinitionResponse response = propertyDefinitionsService.updateDefinition(id, request);
        return ResponseEntity.ok(response);
    }

    /**
     * Retrieves a custom property definition by its unique identifier.
     *
     * @param id The unique identifier of the custom property definition to retrieve.
     * @return A ResponseEntity containing the custom property definition and HTTP status 200 (OK).
     */
    @GetMapping("/{id}")
    public ResponseEntity<CustomPropertyDefinitionResponse> getCustomPropertyDefinition(
            @PathVariable String id) {
        CustomPropertyDefinitionResponse response = propertyDefinitionsService.getDefinition(id);
        return ResponseEntity.ok(response);
    }
}