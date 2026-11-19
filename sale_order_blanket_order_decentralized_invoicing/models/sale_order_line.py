# Copyright 2026 Camptocamp SA
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def _get_call_off_lines_invoiced_apart(self):
        """Return the call-off lines of a blanket line invoiced apart.

        Their delivered quantity is invoiced at the addresses of their call-off
        order instead of the blanket order's ones. Only quantities invoiced on
        delivery are split this way: a line invoiced on ordered quantities stays
        invoiced at the blanket order's addresses.

        :return: call-off ``sale.order.line`` recordset
        """
        self.ensure_one()
        if self.order_type != "blanket" or self.product_id.invoice_policy != "delivery":
            return self.browse()
        call_off_lines = (
            self.move_ids.call_off_sale_line_id
            | self.invoice_lines.call_off_sale_line_id
        )
        return call_off_lines.filtered(lambda line: line.order_id._is_invoiced_apart())

    def _get_call_off_qty_delivered(self, call_off_line):
        """Return the quantity delivered for a call-off line by this blanket line.

        Same computation as ``sale_stock`` for the blanket line, restricted to
        the moves of the call-off line. Returns are copies of the delivered
        moves, so they keep their ``call_off_sale_line_id``.

        :param call_off_line: call-off line
        :return: quantity in the unit of measure of the blanket line
        """
        self.ensure_one()
        outgoing_moves, incoming_moves = self._get_outgoing_incoming_moves()
        qty = 0.0
        for move in outgoing_moves:
            if move.state == "done" and move.call_off_sale_line_id == call_off_line:
                qty += move.product_uom._compute_quantity(
                    move.quantity, self.product_uom_id, rounding_method="HALF-UP"
                )
        for move in incoming_moves:
            if move.state == "done" and move.call_off_sale_line_id == call_off_line:
                qty -= move.product_uom._compute_quantity(
                    move.quantity, self.product_uom_id, rounding_method="HALF-UP"
                )
        return qty

    def _get_call_off_qty_invoiced(self, call_off_line):
        """Return the quantity of this blanket line invoiced for a call-off line.

        Counts the invoice lines tagged with the call-off line: invoices add,
        credit notes subtract, cancelled entries are ignored.

        :param call_off_line: call-off line
        :return: quantity in the unit of measure of the blanket line
        """
        self.ensure_one()
        qty = 0.0
        for invoice_line in self.invoice_lines:
            move = invoice_line.move_id
            if (
                invoice_line.call_off_sale_line_id != call_off_line
                or move.state == "cancel"
            ):
                continue
            invoice_qty = invoice_line.product_uom_id._compute_quantity(
                invoice_line.quantity, self.product_uom_id, round=False
            )
            if move.move_type == "out_invoice":
                qty += invoice_qty
            elif move.move_type == "out_refund":
                qty -= invoice_qty
        return qty

    def _get_call_off_invoice_split(self):
        """Split the quantity to invoice of a blanket line per call-off invoiced apart.

        Example: 10 units delivered, 3 of them for a call-off invoiced in Paris
        and nothing invoiced yet returns ``{paris call-off line: 3.0}``. The
        other 7 units of ``qty_to_invoice`` stay invoiced at the blanket order's
        addresses.

        :return: dict {call-off line: quantity to invoice}, zero quantities left out
        """
        self.ensure_one()
        split = {}
        for call_off_line in self._get_call_off_lines_invoiced_apart().sorted("id"):
            qty = self._get_call_off_qty_delivered(
                call_off_line
            ) - self._get_call_off_qty_invoiced(call_off_line)
            if not self.product_uom_id.is_zero(qty):
                split[call_off_line] = qty
        return split

    def _is_call_off_qty_invoiceable(self, qty, final=False):
        """Apply the rule of ``sale`` on the quantity to invoice of a line.

        :param qty: quantity to invoice
        :param final: whether negative quantities are invoiced as credit notes
        :return: True when the quantity is invoiced
        """
        self.ensure_one()
        compare = self.product_uom_id.compare(qty, 0.0)
        return compare > 0 or (final and compare < 0)

    def _get_call_off_invoice_quantities(self, addresses, final=False):
        """Return the quantities of this blanket line to invoice at some addresses.

        :param addresses: tuple (invoice partner id, shipping partner id)
        :param final: whether negative quantities are invoiced as credit notes
        :return: list of (call-off line, quantity), the call-off line is empty
            for the quantity invoiced at the blanket order's own addresses
        """
        self.ensure_one()
        split = self._get_call_off_invoice_split()
        if addresses == self.order_id._get_invoice_address_ids():
            quantities = [(self.browse(), self.qty_to_invoice - sum(split.values()))]
        else:
            quantities = [
                (call_off_line, qty)
                for call_off_line, qty in split.items()
                if call_off_line.order_id._get_invoice_address_ids() == addresses
            ]
        return [
            (call_off_line, qty)
            for call_off_line, qty in quantities
            if self._is_call_off_qty_invoiceable(qty, final=final)
        ]

    def _prepare_invoice_lines_vals_list(self, **optional_values):
        # OVERRIDE: while a blanket order is invoiced at some addresses, invoice
        # one line per call-off of these addresses. The invoice line keeps its
        # call-off line, so a delivered quantity is invoiced only once.
        addresses = self.env.context.get("call_off_invoice_addresses")
        if (
            not addresses
            or self.order_type != "blanket"
            or self.display_type
            or self.is_downpayment
        ):
            return super()._prepare_invoice_lines_vals_list(**optional_values)
        final = self.env.context.get("call_off_invoice_final", False)
        vals_list = []
        for call_off_line, qty in self._get_call_off_invoice_quantities(
            addresses, final=final
        ):
            vals = self._prepare_invoice_line(**{**optional_values, "quantity": qty})
            if call_off_line:
                vals["call_off_sale_line_id"] = call_off_line.id
                vals["name"] = f"{vals['name']}\n{call_off_line.order_id.name}"
            vals_list.append(vals)
        return vals_list
