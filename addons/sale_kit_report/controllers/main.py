import io
import xlsxwriter
from odoo import http
from odoo.http import request

class SaleKitController(http.Controller):

    @http.route('/web/export_kit_xlsx/<int:order_id>', type='http', auth='user')
    def export_kit_xlsx(self, order_id):
        order = request.env['sale.order'].browse(order_id)
        if not order.exists():
            return request.not_found()

        data = order.get_kit_breakdown_data(order_id)
        lines = data.get('lines', [])

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        sheet = workbook.add_worksheet(f'Kit Breakdown {order.name}')

        header_style = workbook.add_format({'bold': True, 'bg_color': '#714B67', 'font_color': 'white', 'border': 1, 'align': 'center'})
        parent_text = workbook.add_format({'bold': True, 'border': 1})
        parent_num = workbook.add_format({'bold': True, 'num_format': '#,##0.00', 'border': 1})
        child_text = workbook.add_format({'border': 1})
        child_num = workbook.add_format({'num_format': '#,##0.00', 'border': 1})
        section_style = workbook.add_format({'bold': True, 'bg_color': '#e9ecef', 'border': 1})

        sheet.set_column(0, 0, 45)
        sheet.set_column(1, 1, 25)
        sheet.set_column(2, 3, 15)

        headers = ['Product / Component', 'Type', 'Quantity', 'UoM']
        for col, text in enumerate(headers):
            sheet.write(0, col, text, header_style)

        row_idx = 1
        for line in lines:
            display_type = line.get('display_type')
            indent_level = line.get('indent', 0)

            # Lógica para Secciones y Subsecciones
            if display_type == 'line_section':
                prefix = "    " * indent_level # Agrega espacios si es subsección
                section_name = f"{prefix}{line['product_name']}"
                sheet.merge_range(row_idx, 0, row_idx, 3, section_name, section_style)
                row_idx += 1
                continue

            is_line = line['type'] == 'line'
            t_style = parent_text if is_line else child_text
            n_style = parent_num if is_line else child_num

            if indent_level == 0:
                product_name = line['product_name']
            else:
                spaces = "    " * indent_level
                product_name = f"{spaces}↳ {line['product_name']}"

            sheet.write(row_idx, 0, product_name, t_style)
            sheet.write(row_idx, 1, line.get('item_type', ''), t_style)
            sheet.write(row_idx, 2, line['quantity'], n_style)
            sheet.write(row_idx, 3, line['uom'], t_style)
            row_idx += 1

        workbook.close()
        output.seek(0)

        file_name = f'Sale_Kit_Breakdown_{order.name}.xlsx'
        return request.make_response(
            output.getvalue(),
            headers=[
                ('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                ('Content-Disposition', f'attachment; filename={file_name};')
            ]
        )