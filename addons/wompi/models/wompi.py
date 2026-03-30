# -*- coding: utf-8 -*-
from datetime import datetime
from odoo import api,fields,models,_

class Wompi(models.Model):
    _name = 'wompi'
    _description = 'Wompi'

    name = fields.Char(string="Name", default=lambda self: _('New'), readonly=True, copy=False,)
    state = fields.Selection([('draft', 'Draft'),('confirmed', 'Confirmed'),('done', 'Done')],
     default='draft', string="State")
    description = fields.Text(string="Description")
    amount = fields.Float(string="Amount")
    active = fields.Boolean(default=True)
    date = fields.Date(string="Date", default=fields.Date.context_today)

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override the create method to assign a sequence-generated name 
        to each record being created.

        :param vals_list: List of dictionaries with field values for new records.
        :return: Recordset of newly created records.
        """
        for vals in vals_list:
            sequence = self.env['ir.sequence'].next_by_code('wompi')
            vals['name'] = sequence or _('New')
        return super().create(vals_list)

    def write(self, vals):
        """
        Override the write method to include custom behavior when updating records.

        :param vals: Dictionary of field values to update.
        :return: True if the write was successful.
        """
        return super().write(vals)

    def unlink(self):
        """
        Override the unlink method to include custom behavior when deleting records.

        :return: True if the records were successfully deleted.
        """
        return super().unlink()

    def action_do_something(self):
        self.ensure_one()
        # Placeholder for button action
        pass

    def cron_sample_method(self):
        # Placeholder for scheduled action
        pass
