from odoo import models, fields, api

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_export_kit_xlsx(self):
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/export_kit_xlsx/{self.id}',
            'target': 'new',
        }

    @api.model
    def get_kit_breakdown_data(self, order_id):
        order = self.browse(order_id)
        structure = []
        unique_id = 1

        def _get_selection_label(record, field_name):
            if not record or not field_name:
                return ''
            field = record._fields.get(field_name)
            val = getattr(record, field_name, '')
            if not field or not field.selection:
                return str(val or '')

            selection = field.selection
            if callable(selection):
                try:
                    selection = selection(record)
                except Exception:
                    selection = []
            sel_dict = dict(selection) if selection else {}
            return str(sel_dict.get(val, val) or '')

        def _get_type_label(product, bom):
            if not product:
                return ''
            prod_field = 'detailed_type' if 'detailed_type' in product._fields else 'type'
            prod_label = _get_selection_label(product, prod_field)
            if bom:
                bom_label = _get_selection_label(bom, 'type')
                return f"{bom_label} / {prod_label}"
            return prod_label

        def _get_bom_lines(product, qty, parent_id, indent, current_id, company):
            lines = []
            if not product:
                return lines, current_id

            boms_dict = self.env['mrp.bom'].sudo()._bom_find(product, company_id=company.id)
            bom = boms_dict.get(product)

            if not bom:
                return lines, current_id

            factor = qty / (bom.product_qty or 1.0)

            for bom_line in bom.bom_line_ids:
                line_qty = bom_line.product_qty * factor
                line_product = bom_line.product_id

                child_boms_dict = self.env['mrp.bom'].sudo()._bom_find(line_product, company_id=company.id)
                child_bom = child_boms_dict.get(line_product)
                has_bom = bool(child_bom)

                lines.append({
                    'id': current_id,
                    'parent_id': parent_id,
                    'type': 'component',
                    'display_type': False,
                    'indent': indent,
                    'product_name': line_product.display_name or '',
                    'item_type': _get_type_label(line_product, child_bom),
                    'quantity': line_qty,
                    'uom': bom_line.product_uom_id.name or '',
                    'unit_price': line_product.standard_price,
                    'amount_total': line_qty * line_product.standard_price,
                    'has_bom': has_bom
                })

                this_line_id = current_id
                current_id += 1

                if has_bom:
                    child_lines, current_id = _get_bom_lines(line_product, line_qty, this_line_id, indent + 1, current_id, company)
                    lines.extend(child_lines)

            return lines, current_id

        for line in order.order_line:
            parent_id = unique_id

            display_type = line.display_type or ''

            # Ignorar Notas
            if display_type == 'line_note':
                continue

            # Detectar Secciones, Subsecciones o líneas de texto sin producto
            if 'section' in display_type or not line.product_id:
                # Le damos nivel de sangría 1 si es una subsección explícita
                indent_level = 1 if 'sub' in display_type else 0

                structure.append({
                    'id': parent_id,
                    'type': 'line',
                    'display_type': 'line_section', # Forzamos para que el XML lo dibuje como bloque ancho
                    'indent': indent_level,
                    'product_name': line.name or '', # Extraemos el nombre real, evitamos 'false'
                    'item_type': '',
                    'quantity': 0,
                    'uom': '',
                    'unit_price': 0,
                    'amount_total': 0,
                    'has_bom': False
                })
                unique_id += 1
                continue

            boms_dict = self.env['mrp.bom'].sudo()._bom_find(line.product_id, company_id=order.company_id.id)
            bom = boms_dict.get(line.product_id)
            has_bom = bool(bom)

            structure.append({
                'id': parent_id,
                'type': 'line',
                'display_type': False,
                'indent': 0,
                'product_name': line.product_id.display_name or line.name or '',
                'item_type': _get_type_label(line.product_id, bom),
                'quantity': line.product_uom_qty,
                'uom': line.product_uom_id.name or '',
                'unit_price': line.price_unit,
                'amount_total': line.price_subtotal,
                'has_bom': has_bom
            })
            unique_id += 1

            if has_bom:
                child_lines, unique_id = _get_bom_lines(
                    line.product_id, line.product_uom_qty, parent_id, 1, unique_id, order.company_id
                )
                structure.extend(child_lines)

        return {
            'lines': structure,
            'amount_untaxed': order.amount_untaxed,
            'amount_tax': order.amount_tax,
            'amount_total': order.amount_total,
        }