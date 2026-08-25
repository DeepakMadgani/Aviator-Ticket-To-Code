"""Define commonly used Pydantic models for the AI tools.

This file contains example Pydantic models that can be used for
input validation and structured outputs in your custom tools.
"""

from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

# Example models for your custom tools


class CalculationInput(BaseModel):
    """Example input model for a calculator tool."""

    operation: Literal["add", "subtract", "multiply", "divide"] = Field(
        ..., description="Mathematical operation to perform"
    )
    x: float = Field(..., description="First operand")
    y: float = Field(..., description="Second operand")

    @field_validator("y")
    @classmethod
    def validate_division(cls, v: float, info: ValidationInfo) -> float:
        """Prevent division by zero."""
        if info.data.get("operation") == "divide" and v == 0:
            msg = "Cannot divide by zero"
            raise ValueError(msg)
        return v


class CalculationResult(BaseModel):
    """Example output model for calculation results."""

    operation: str = Field(..., description="Operation that was performed")
    input_x: float = Field(..., description="First operand")
    input_y: float = Field(..., description="Second operand")
    result: float = Field(..., description="Calculation result")
    expression: str = Field(..., description="String representation of the calculation")
