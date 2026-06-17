"""
ADT Aviator LLM Client
Connects to ADT Aviator model for classification and code generation
"""
import os
import json
from typing import Dict, Any, Optional
import httpx

class ADTAviatorClient:
    """
    Client for ADT Aviator LLM API.
    Handles ticket classification and code patch generation.
    """
    
    def __init__(self, api_endpoint: Optional[str] = None, api_key: Optional[str] = None):
        """
        Initialize ADT Aviator client.
        
        Args:
            api_endpoint: API endpoint URL (defaults to env var ADT_AVIATOR_ENDPOINT)
            api_key: API key (defaults to env var ADT_AVIATOR_API_KEY)
        """
        self.api_endpoint = api_endpoint or os.getenv('ADT_AVIATOR_ENDPOINT', 'http://localhost:8080/v1')
        self.api_key = api_key or os.getenv('ADT_AVIATOR_API_KEY', '')
        self.timeout = 60.0
    
    async def classify_operation(self, ticket_description: str, 
                                context: Optional[Dict[str, Any]] = None) -> str:
        """
        Classify the type of operation needed for a ticket.
        
        Args:
            ticket_description: The ticket text
            context: Optional domain context from RAG
            
        Returns:
            Operation type: 'CODE_MODIFICATION', 'CODE_ADDITION', 'BUG_FIX', 'REFACTORING'
        """
        prompt = self._build_classification_prompt(ticket_description, context)
        
        response = await self._call_llm(
            prompt=prompt,
            temperature=0.1,
            max_tokens=50
        )
        
        # Parse response
        operation = self._parse_classification(response)
        return operation
    
    async def generate_patch(self, ticket_description: str, 
                           context: Dict[str, Any]) -> str:
        """
        Generate code patch for the ticket.
        
        Args:
            ticket_description: The ticket text
            context: Expanded context including:
                - target_files: Code to modify
                - dependencies: Related code
                - architecture_rules: Architecture guidelines
                - business_rules: Domain rules
                
        Returns:
            Generated code patch
        """
        prompt = self._build_generation_prompt(ticket_description, context)
        
        response = await self._call_llm(
            prompt=prompt,
            temperature=0.2,
            max_tokens=2000
        )
        
        return response
    
    def _build_classification_prompt(self, ticket: str, context: Optional[Dict] = None) -> str:
        """Build prompt for classification."""
        prompt = f"""You are a code change classifier for the Supplier Exchange platform.

# TICKET
{ticket}

# DOMAIN CONTEXT
"""
        if context and context.get('domain_info'):
            prompt += context['domain_info']
        else:
            prompt += "No specific domain context available."
        
        prompt += """

# TASK
Classify this ticket into ONE of these categories:
- CODE_MODIFICATION: Modify existing code (change logic, update validation, fix behavior)
- CODE_ADDITION: Add new code (new feature, new endpoint, new class)
- BUG_FIX: Fix a bug or error
- REFACTORING: Restructure code without changing behavior

Respond with ONLY the category name, nothing else.
"""
        return prompt
    
    def _build_generation_prompt(self, ticket: str, context: Dict[str, Any]) -> str:
        """Build prompt for code generation."""
        prompt = f"""You are a Java code generator for the Supplier Exchange platform.

# TASK
{ticket}

# CODE TO MODIFY
```java
{context.get('target_code', '')}
```

# DEPENDENCIES
"""
        for dep in context.get('dependencies', [])[:3]:
            prompt += f"\n```java\n{dep}\n```\n"
        
        prompt += f"""

# ARCHITECTURE RULES (MUST FOLLOW)
{context.get('architecture_rules', 'No specific rules provided.')}

# CODING STANDARDS (MUST FOLLOW)
{context.get('coding_standards', 'Follow standard Java conventions.')}

# BUSINESS RULES (MUST PRESERVE)
{context.get('business_rules', 'No specific business rules.')}

# INSTRUCTIONS
Generate ONLY the modified method code. Do not include class declaration or imports.
Preserve all existing functionality unless explicitly asked to change it.
Follow all architecture and coding standards above.
Maintain proper error handling and validation.

Output ONLY the Java code, no explanations.
"""
        return prompt
    
    async def _call_llm(self, prompt: str, temperature: float = 0.2, 
                       max_tokens: int = 1000) -> str:
        """
        Call the ADT Aviator LLM API.
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {
                    'Content-Type': 'application/json',
                }
                if self.api_key:
                    headers['Authorization'] = f'Bearer {self.api_key}'
                
                payload = {
                    'model': 'adt-aviator',
                    'prompt': prompt,
                    'temperature': temperature,
                    'max_tokens': max_tokens
                }
                
                response = await client.post(
                    f"{self.api_endpoint}/completions",
                    headers=headers,
                    json=payload
                )
                
                if response.status_code != 200:
                    # Fallback: return mock response for testing
                    return self._mock_response(prompt)
                
                result = response.json()
                return result.get('choices', [{}])[0].get('text', '').strip()
        
        except Exception as e:
            print(f"⚠️  LLM call failed: {e}, using mock response")
            return self._mock_response(prompt)
    
    def _mock_response(self, prompt: str) -> str:
        """Mock response for testing when API is unavailable."""
        if "classify" in prompt.lower() or "category" in prompt.lower():
            # Classification mock
            if "bug" in prompt.lower() or "fix" in prompt.lower() or "error" in prompt.lower():
                return "BUG_FIX"
            elif "add" in prompt.lower() or "new" in prompt.lower() or "create" in prompt.lower():
                return "CODE_ADDITION"
            elif "refactor" in prompt.lower() or "restructure" in prompt.lower():
                return "REFACTORING"
            else:
                return "CODE_MODIFICATION"
        else:
            # Generation mock
            return """public void updateValidation() {
    // TODO: Generated code patch
    // Validate input parameters
    if (input == null || input.isEmpty()) {
        throw new ValidationException("Input cannot be null or empty");
    }
    // Process validation logic
    validate(input);
}"""
    
    def _parse_classification(self, response: str) -> str:
        """Parse classification response."""
        response_upper = response.upper().strip()
        
        valid_types = ['CODE_MODIFICATION', 'CODE_ADDITION', 'BUG_FIX', 'REFACTORING']
        
        for op_type in valid_types:
            if op_type in response_upper:
                return op_type
        
        # Default
        return 'CODE_MODIFICATION'
