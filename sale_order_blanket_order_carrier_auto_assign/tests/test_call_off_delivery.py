import freezegun

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from odoo.addons.sale_order_blanket_order.tests.common import (
    SaleOrderBlanketOrderCase,
)


@tagged("post_install", "-at_install")
class TestCallOffDelivery(SaleOrderBlanketOrderCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.delivery_product = cls.env["product.product"].create(
            {
                "name": "Delivery fees",
                "type": "service",
                "invoice_policy": "order",
            }
        )
        cls.carrier = cls.env["delivery.carrier"].create(
            {
                "name": "Test Carrier",
                "delivery_type": "fixed",
                "product_id": cls.delivery_product.id,
                # Keep it zero because of `_check_call_off_order_line_price`
                # Otherwise, error will raise before the test
                "fixed_price": 0.0,
            }
        )
        # Configure partner's carrier
        cls.partner.property_delivery_carrier_id = cls.carrier
        # Configure company settings
        company = cls.env.company
        company.carrier_on_create = True
        company.carrier_auto_assign = True
        company.create_call_off_from_so_if_possible = True
        # Confirm to trigger delivery fee (`_is_auto_set_carrier_on_confirm`)
        cls.blanket_so.action_confirm()

    def _make_regular_so(self, qty):
        return self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.product_1.id,
                            "product_uom_qty": qty,
                            "price_unit": 100.0,
                        }
                    ),
                ],
            }
        )

    @freezegun.freeze_time("2025-06-01")
    def test_two_sales_with_calloff_without_fix(self):
        """Confirm the presence of the bug by deactivating the module's fix"""
        self.assertTrue(any(self.blanket_so.order_line.mapped("is_delivery")))

        so1 = self._make_regular_so(qty=5)
        so1.with_context(skip_blanket_carrier_filter=True).action_confirm()

        so2 = self._make_regular_so(qty=5)
        with self.assertRaisesRegex(
            ValidationError, "not part of linked blanket order"
        ):
            so2.with_context(skip_blanket_carrier_filter=True).action_confirm()

    @freezegun.freeze_time("2025-06-01")
    def test_two_sales_with_calloff_with_fix(self):
        """Consecutive SOs must be able to generate Call-Offs
        despite the delivery fees"""
        self.assertTrue(any(self.blanket_so.order_line.mapped("is_delivery")))

        so1 = self._make_regular_so(qty=5)
        so1.action_confirm()
        so2 = self._make_regular_so(qty=5)
        so2.action_confirm()

        self.assertEqual(len(self.blanket_so.call_off_order_ids), 2)
        for co in self.blanket_so.call_off_order_ids:
            self.assertEqual(co.state, "sale")
