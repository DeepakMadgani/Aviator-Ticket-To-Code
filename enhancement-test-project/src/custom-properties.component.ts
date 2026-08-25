SEARCH:
```typescript
  customProperty: any;
```
REPLACE:
```typescript
  customProperty: any;
  validationTypes: string[] = ['None', 'Regex', 'Min/Max'];
```
---
SEARCH:
```typescript
  ngOnInit(): void {
    // Existing ngOnInit logic
  }
```
REPLACE:
```typescript
  ngOnInit(): void {
    // Existing ngOnInit logic
    if (!this.customProperty) {
      this.customProperty = {};
    }
    if (!this.customProperty.validationType) {
      this.customProperty.validationType = 'None';
    }
    if (this.customProperty.validationPattern === undefined) {
      this.customProperty.validationPattern = '';
    }
    if (this.customProperty.minValue === undefined) {
      this.customProperty.minValue = null;
    }
    if (this.customProperty.maxValue === undefined) {
      this.customProperty.maxValue = null;
    }
  }
```
---
SEARCH:
```typescript
  onValidationTypeChange(event: any): void {
    // Existing logic, if any
  }
```
REPLACE:
```typescript
  onValidationTypeChange(event: any): void {
    this.customProperty.validationType = event.target.value;

    ```typescript
  onValidationTypeChange(event: any): void {
    this.customProperty.validationType = event.target.value;

    // Clear validation fields based on the new validation type
    if (this.customProperty.validationType === 'None') {
      this.customProperty.validationPattern = '';
      this.customProperty.minValue = null;
      this.customProperty.maxValue = null;
    } else if (this.customProperty.validationType === 'Regex') {
      this.customProperty.minValue = null;
      this.customProperty.maxValue = null;
    } else if (this.customProperty.validationType === 'Min/Max') {
      this.customProperty.validationPattern = '';
    }
  }
```