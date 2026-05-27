from odoo import models, fields

class NuveiBatchCloseReport(models.Model):
    _name = 'nuvei.batch.close.report'
    _description = 'Nuvei Batch Close Report'
    _order = 'date desc'

    date = fields.Datetime('Date', default=fields.Datetime.now)
    payment_method_id = fields.Many2one('pos.payment.method', 'Payment Method')
    status = fields.Char('Status')
    sales_count = fields.Integer('Sales')
    sales_amount = fields.Float('Sales Amount')
    refunds_count = fields.Integer('Refunds')
    refunds_amount = fields.Float('Refund Amount')
    net_count = fields.Integer('Net')
    net_amount = fields.Float('Net Amount')
    raw_response = fields.Text('Full Response')
