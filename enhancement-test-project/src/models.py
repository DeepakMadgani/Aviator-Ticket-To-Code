# ──────────────────────────────────────────
# Database Models
# ──────────────────────────────────────────
"""
Database models for the order service.
Intentionally has a type error in the discount method (tests causal chain).
"""

import os
from datetime import datetime
from typing import Optional, List


class OrderModel:
    """Represents an order in the database."""

    def __init__(self, order_id: int, customer: str, total: float):
        self.order_id = order_id
        self.customer = customer
        self.total = total
        self.created_at = datetime.now()
        self.status = "pending"
        self.items: List[dict] = []

    def apply_discount(self, discount_percent: float) -> float:
        """
        Apply a discount to the order total.
        Applies a 15% discount if the order total exceeds $100, otherwise uses the provided discount_percent.
        Ensures the calculated discount does not exceed the current order total,
        preventing negative totals.
        """
        effective_discount_percent = discount_percent

        # Apply 15% discount if total exceeds $100
        if self.total > 100.0:
            effective_discount_percent = 15.0

        # Ensure effective_discount_percent is non-negative
        if effective_discount_percent < 0:
            effective_discount_percent = 0.0

        # Calculate the potential discount amount
        potential_discount_amount = self.total * (effective_discount_percent / 100)

        # Cap the discount amount to not exceed the current total
        actual_discount_amount = min(potential_discount_amount, self.total)

        # Apply the discount
        self.total -= actual_discount_amount
        # Round the total to two decimal places to ensure currency precision
        self.total = round(self.total, 2)
        return self.total

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "customer": self.customer,
            "total": self.total,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "items": self.items,
        }


class CustomerModel:
    """Represents a customer."""

    def __init__(self, customer_id: int, name: str, email: str):
        self.customer_id = customer_id
        self.name = name
        self.email = email
        self.orders: List[int] = []
        self.tier = "standard"  # standard | premium | enterprise

    def get_discount_rate(self) -> float:
        """Get discount rate based on customer tier."""
        rates = {
            "standard": 0,
            "premium": 10,
            "enterprise": 20,
        }
        return rates.get(self.tier, 0)

    def to_dict(self) -> dict:
        return {
            "customer_id": self.customer_id,
            "name": self.name,
            "email": self.email,
            "tier": self.tier,
            "orders": self.orders,
        }


class InventoryItem:
    """Represents an item in inventory."""

    def __init__(self, item_id: str, name: str, price: float, stock: int):
        self.item_id = item_id
        self.name = name
        self.price = price
        self.stock = stock

    def reserve(self, quantity: int) -> bool:
        """Reserve stock for an order."""
        if quantity > self.stock:
            return False
        self.stock -= quantity
        return True

    def restock(self, quantity: int):
        """Add stock."""
        self.stock += quantity


class ProjectMember:
    """Represents a member created at the Project level with an assigned organization."""

    def __init__(self, member_id: int, name: str, organization: str):
        self.member_id = member_id
        self.name = name
        self.organization = organization

    def to_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "name": self.name,
            "organization": self.organization,
        }



