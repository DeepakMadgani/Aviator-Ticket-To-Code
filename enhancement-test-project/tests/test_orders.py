# Order Service - Test Suite
"""
Tests for the order service.
"""

import pytest
from src.app import OrderService, MemberService
from src.models import OrderModel, CustomerModel, InventoryItem, ProjectMember


class TestOrderService:
    """Tests for OrderService."""

    def setup_method(self):
        self.service = OrderService()

    def test_create_order(self):
        order = self.service.create_order("Alice", ["Widget"], 29.99) # Removed customer_location, added customer_name
        assert order["id"] == 1
        assert order["customer"] == "Alice"
        assert order["status"] == "pending"
        # Assuming a default TAX_RATE of 0.10 for this test
        # 29.99 * 0.10 = 2.999, rounded to 3.00
        assert order["total"] == 32.99  # 29.99 + 3.00
        assert order["tax_amount"] == 3.00

    def test_get_order(self):
        self.service.create_order("Bob", ["Gadget"], 49.99)
        order = self.service.get_order(1)
        assert order is not None
        assert order["customer"] == "Bob"

    def test_get_nonexistent_order(self):
        order = self.service.get_order(999)
        assert order is None

    def test_update_status(self):
        self.service.create_order("Charlie", ["Thing"], 19.99)
        updated = self.service.update_status(1, "shipped")
        assert updated["status"] == "shipped"

    def test_update_status_not_found(self):
        with pytest.raises(ValueError):
            self.service.update_status(999, "shipped")

    def test_calculate_tax(self):
        tax = self.service.calculate_tax(100.0, "USA") # Changed customer_location to customer_name
        # Assuming a default TAX_RATE of 0.10 for this test
        assert tax == 10.0  # 100.0 * 0.10

    def test_get_all_orders(self):
        self.service.create_order("Dave", ["A"], 10.0)
        self.service.create_order("Eve", ["B"], 20.0)
        orders = self.service.get_all_orders()
        assert len(orders) == 2


class TestOrderModel:
    """Tests for OrderModel."""

    def test_apply_discount(self):
        order = OrderModel(1, "Test", 100.0)
        result = order.apply_discount(10)
        assert result == 90.0

    def test_apply_zero_discount(self):
        order = OrderModel(1, "Test", 100.0)
        result = order.apply_discount(0)
        assert result == 100.0

    def test_to_dict(self):
        order = OrderModel(1, "Test", 50.0)
        d = order.to_dict()
        assert d["order_id"] == 1
        assert d["customer"] == "Test"
        assert d["total"] == 50.0


class TestCustomerModel:
    """Tests for CustomerModel."""

    def test_standard_discount(self):
        c = CustomerModel(1, "Alice", "alice@test.com")
        assert c.get_discount_rate() == 0

    def test_premium_discount(self):
        c = CustomerModel(1, "Bob", "bob@test.com")
        c.tier = "premium"
        assert c.get_discount_rate() == 10

    def test_enterprise_discount(self):
        c = CustomerModel(1, "Corp", "corp@test.com")
        c.tier = "enterprise"
        assert c.get_discount_rate() == 20


class TestInventoryItem:
    """Tests for InventoryItem."""

    def test_reserve_success(self):
        item = InventoryItem("W001", "Widget", 9.99, 10)
        assert item.reserve(5) is True
        assert item.stock == 5

    def test_reserve_insufficient(self):
        item = InventoryItem("W001", "Widget", 9.99, 3)
        assert item.reserve(5) is False
        assert item.stock == 3

    def test_restock(self):
        item = InventoryItem("W001", "Widget", 9.99, 5)
        item.restock(10)
        assert item.stock == 15


class TestMemberService:
    """Tests for MemberService and project-to-contract member mapping."""

    def setup_method(self):
        self.service = MemberService()

    def test_get_project_member(self):
        member = self.service.get_project_member(1)
        assert member is not None
        assert member.name == "Alice Supplier"
        assert member.organization == "Acme Corp"

    def test_assign_member_without_override(self):
        res = self.service.assign_member_to_contract(101, 1)
        assert res["contract_id"] == 101
        assert res["member_id"] == 1
        assert res["organization"] == "Acme Corp"
