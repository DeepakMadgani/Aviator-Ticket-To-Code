# ──────────────────────────────────────────
# Order Service - Main App Entry Point
# ──────────────────────────────────────────
"""
A mini HTTP order service. Has several intentional issues
that Aviator's enhancement agents should detect and fix.
"""

import os
import json
import logging
import sys # Added for sys.exit
from typing import Optional, List, Dict
from http.server import HTTPServer, BaseHTTPRequestHandler
from src.models import ProjectMember
from src.utils import load_config
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

logger = logging.getLogger(__name__)


class OrderService:
    """Core order management logic."""

    def __init__(self):
        self.orders = {}
        self.next_id = 1
        self.db_url = os.environ.get("DATABASE_URL", "sqlite:///orders.db")
        self.api_key = os.environ.get("API_KEY")
        # Load TAX_RATE from environment, with a default of 0.10
        tax_rate_str = os.environ.get("TAX_RATE", "0.10")
        try:
            self.tax_rate = Decimal(tax_rate_str)
        except InvalidOperation:
            logger.warning(f"Invalid TAX_RATE environment variable '{tax_rate_str}'. Falling back to 0.10.")
            self.tax_rate = Decimal("0.10")
        logger.info(f"OrderService initialized with TAX_RATE: {self.tax_rate}")

    def create_order(self, customer_name: str, items: list, total: float) -> dict:
        """Create a new order."""
        # Calculate tax using the refactored method
        tax_amount_float = self.calculate_tax(total, customer_name)

        # Convert all relevant floats to Decimal for precise arithmetic
        total_decimal = Decimal(str(total))
        tax_amount_decimal = Decimal(str(tax_amount_float))
        two_places = Decimal("0.01") # Represents two decimal places for quantize

        # Perform addition and rounding using Decimal
        total_with_tax_decimal = (total_decimal + tax_amount_decimal).quantize(two_places, rounding=ROUND_HALF_UP)

        # Convert back to float for storage, maintaining consistency with existing data types
        total_with_tax = float(total_with_tax_decimal)
        tax_amount = float(tax_amount_decimal) # Ensure tax_amount is also float for consistency

        order = {
            "id": self.next_id,
            "customer": customer_name,
            "items": items,
            "total": total_with_tax,
            "tax_amount": tax_amount, # Add tax amount for transparency
            "status": "pending",
        }
        self.orders[self.next_id] = order
        self.next_id += 1
        logger.info(f"Created order {order['id']} for {customer_name} with total {total_with_tax} (tax: {tax_amount})")
        return order

    def get_order(self, order_id: int) -> dict:
        """Get order by ID. Returns None if not found."""
        return self.orders.get(order_id)

    def update_status(self, order_id: int, new_status: str) -> dict:
        """Update order status."""
        order = self.orders.get(order_id)
        if order is None:
            raise ValueError(f"Order {order_id} not found")
        order["status"] = new_status
        return order

    def calculate_tax(self, amount: float, customer_name: str) -> float:
        """
        Calculate tax on an amount. Uses TAX_RATE env var and customer-specific rules.
        The customer_name parameter is now used for more sophisticated tax logic.
        """
        # Define tax rates using Decimal for precision
        amount_decimal = Decimal(str(amount))
        two_places = Decimal("0.01") # Represents two decimal places for quantize

        if customer_name == "VIP_Customer":
            rate = Decimal("0.05") # 5% tax for VIPs
        elif customer_name == "International_Customer":
            rate = Decimal("0.00") # No tax for international
        else:
            # Default rate from environment, loaded during initialization
            rate = self.tax_rate

        tax_amount_decimal = (amount_decimal * rate).quantize(two_places, rounding=ROUND_HALF_UP)
        return float(tax_amount_decimal)

    def get_all_orders(self) -> list:
        """Return all orders."""
        return list(self.orders.values())


class NotificationService:
    """Sends notifications to external services (cross-system call)."""

    def __init__(self):
        config = load_config()
        # Prefer config value, fallback to environment variable
        notification_url_from_config = config.get("notification_service_url")
        notification_url_from_env = os.environ.get("NOTIFICATION_SERVICE_URL")

        self.notification_url = notification_url_from_config or notification_url_from_env

        if not self.notification_url:
            logger.error(
                "Notification service URL not found in config/settings.json "
                "or as NOTIFICATION_SERVICE_URL environment variable. Exiting."
            )
            sys.exit(1)
        logger.info(f"Notification Service initialized with URL: {self.notification_url}")

    def send_order_confirmation(self, order: dict) -> bool:
        """Send order confirmation email via notification microservice."""
        import requests  # <-- intentionally NOT in requirements.txt (tests Enhancement 12)
        try:
            response = requests.post(
                self.notification_url,
                json={
                    "type": "order_confirmation",
                    "order_id": order["id"],
                    "customer": order["customer"],
                    "total": order["total"],
                }
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
            return False


class PaymentGateway:
    """Processes payments via external gateway (cross-system call)."""

    def __init__(self):
        # Use os.environ.get to gracefully handle missing variables, validation will catch it.
        self.gateway_url = os.environ.get("PAYMENT_GATEWAY_URL")

    def charge(self, order_id: int, amount: float, currency: str = "USD") -> dict:
        """Charge customer for an order."""
        import requests  # cross-system HTTP call
        response = requests.post(
            self.gateway_url,
            json={"order_id": order_id, "amount": amount, "currency": currency}
        )
        return response.json()


class MemberService:
    """Manages Project Members and Contract Member assignments."""

    def __init__(self):
        # Canonical project-level member registry
        self.project_members = {
            1: ProjectMember(1, "Alice Supplier", "Acme Corp"),
            2: ProjectMember(2, "Bob Contractor", "Global Logistics"),
        }
        self.contract_members = {}

    def get_project_member(self, member_id: int) -> Optional[ProjectMember]:
        """Fetch member from project registry."""
        return self.project_members.get(member_id)

    def assign_member_to_contract(self, contract_id: int, member_id: int, organization: Optional[str] = None) -> dict:
        """
        Assign an existing project member to a contract.
        BUG (CC4E Issue): Currently permits overriding the organization when passed in,
        rather than strictly enforcing the static project-level organization.
        """
        project_member = self.get_project_member(member_id)
        if not project_member:
            raise ValueError(f"Project member {member_id} not found")

        # Fix: Always use the canonical organization from the project member.
        assigned_org = project_member.organization

        contract_member = ContractMember(contract_id, member_id, assigned_org)
        self.contract_members[(contract_id, member_id)] = contract_member
        logger.info(
            f"Assigned member {member_id} to contract {contract_id} with organization '{assigned_org}'"
        )
        return contract_member.to_dict()

    def get_contract_members(self, contract_id: int) -> list:
        """Return all members assigned to a contract."""
        return [
            m.to_dict()
            for (c_id, _), m in self.contract_members.items()
            if c_id == contract_id
        ]


class OrderHandler(BaseHTTPRequestHandler):
    """HTTP request handler for order and member endpoints."""

    service = OrderService()
    member_service = MemberService()

    def do_GET(self):
        if self.path == "/orders":
            orders = self.service.get_all_orders()
            self._json_response(200, orders)
        elif self.path.startswith("/orders/"):
            try:
                order_id = int(self.path.split("/")[-1])
                order = self.service.get_order(order_id)
                if order:
                    self._json_response(200, order)
                else:
                    self._json_response(404, {"error": "Order not found"})
            except ValueError:
                self._json_response(400, {"error": "Invalid order ID"})
        elif self.path == "/health":
            self._json_response(200, {"status": "OK"})
        elif self.path.startswith("/api/contracts/") and self.path.endswith("/members"):
            try:
                contract_id = int(self.path.split("/")[3])
                members = self.member_service.get_contract_members(contract_id)
                self._json_response(200, members)
            except Exception:
                self._json_response(400, {"error": "Invalid contract ID"})
        else:
            self._json_response(404, {"error": "Not found"})

    def do_POST(self):
        if self.path == "/api/orders":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body)
                order = self.service.create_order(
                    customer_name=data["customer"],
                    items=data.get("items", []),
                    total=data.get("total", 0.0),
                )
                self._json_response(201, order)
            except json.JSONDecodeError:
                logger.exception("Failed to decode JSON for order creation.")
                self._json_response(400, {"error": "Invalid JSON format in request body."})
            except KeyError as e:
                logger.exception(f"Missing required field in order creation request: {e}")
                self._json_response(400, {"error": f"Missing required field: {e}"})
            except Exception as e:
                logger.exception(f"An unexpected error occurred during order creation: {e}")
                self._json_response(500, {"error": "Internal server error."})
        elif self.path.startswith("/api/contracts/") and self.path.endswith("/members"):
            try:
                contract_id = int(self.path.split("/")[3])
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                data = json.loads(body)
                result = self.member_service.assign_member_to_contract(
                    contract_id=contract_id,
                    member_id=data["member_id"],
                    organization=data.get("organization"),
                )
                self._json_response(201, result)
            except Exception as e:
                self._json_response(400, {"error": str(e)})
        else:
            self._json_response(404, {"error": "Not found"})

    def _json_response(self, status: int, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))


def _validate_environment_variables():
    """
    Validates that required environment variables are set.
    Logs an error and exits if any are missing or empty.
    """
    required_vars = [
        "NOTIFICATION_SERVICE_URL",
        "PAYMENT_GATEWAY_URL",
    ]
    missing_vars = []
    for var in required_vars:
        value = os.environ.get(var)
        if not value: # Checks for None or empty string
            missing_vars.append(var)

    if missing_vars:
        logger.error(
            f"Missing required environment variables: {', '.join(missing_vars)}. "
            "Please set them before starting the service."
        )
        sys.exit(1)
    else:
        logger.info("All required environment variables are set.")


def main():
    """Start the order service on PORT env var (default 8000)."""
    _validate_environment_variables()
    port = int(os.environ.get("PORT", "8000"))
    server = HTTPServer(("0.0.0.0", port), OrderHandler)
    print(f"Order Service running on http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
