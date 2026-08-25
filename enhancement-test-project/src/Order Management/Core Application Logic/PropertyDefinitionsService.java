SEARCH:
```java
    existingDefinition.setDescription(updatedDefinition.getDescription());
    // Add other fields to update here as needed

    return propertyDefinitionRepository.save(existingDefinition);
}
```
REPLACE:
```java
    existingDefinition.setDescription(updatedDefinition.getDescription());
    // Add other fields to update here as needed
    existingDefinition.setValidationRegex(updatedDefinition.getValidationRegex());

    return propertyDefinitionRepository.save(existingDefinition);
}
```