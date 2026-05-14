from odoo import models

class SaleKitXlsx(models.AbstractModel):
    _name = 'report.sale_kit_report.kit_report_xlsx'
    _inherit = 'report.report_xlsx.abstract'

    def generate_xlsx_report(self, workbook, data, orders):
        for order in orders:
            sheet = workbook.add_worksheet(order.name)

            # Estilos
            header_format = workbook.add_format({'bold': True, 'bg_color': '#714B67', 'font_color': 'white', 'border': 1})
            num_format = workbook.add_format({'num_format': '#,##0.00'})

            # Encabezados
            headers = ['Producto (Venta)', 'Componente', 'Cant. Calculada', 'Costo Base', 'Total Costo']
            for col, text in enumerate(headers):
                sheet.write(0, col, text, header_format)

            # Llenado de datos
            rows = order.get_kit_structure()
            for row_num, line in enumerate(rows, start=1):
                sheet.write(row_num, 0, line['parent'])
                sheet.write(row_num, 1, line['component'])
                sheet.write(row_num, 2, line['qty_solicitada'], num_format)
                sheet.write(row_num, 3, line['costo_unitario'], num_format)
                sheet.write(row_num, 4, line['subtotal_costo'], num_format)