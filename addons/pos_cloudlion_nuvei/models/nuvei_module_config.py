# -*- coding: utf-8 -*-
"""Configuración global del módulo Nuvei."""
from odoo import fields, models, api, _


class NuveiModuleConfig(models.Model):
    """Configuración global del módulo Nuvei (una sola instancia)."""
    _name = 'nuvei.module.config'
    _description = 'Nuvei Module Configuration'
    _rec_name = 'id'

    # =========================================================================
    # CONFIGURACIÓN DE BATCH CLOSE
    # =========================================================================

    batch_close_enabled = fields.Boolean(
        string='Enable Automatic Batch Close',
        default=True,
        help='Enable automatic batch close for all terminals daily.',
    )

    batch_close_hour = fields.Float(
        string='Batch Close Time (Hour of Day)',
        default=23.983,  # 23:59 (23 + 59/60)
        help='Hour of day when batch close should run (0.0-23.999).\n'
             'Example: 23.983 = 11:59 PM',
        required=True,
    )

    @api.constrains('batch_close_hour')
    def _check_batch_close_hour(self):
        """Validar que la hora esté en rango 0-24."""
        for record in self:
            if not (0.0 <= record.batch_close_hour <= 23.999):
                raise ValueError(_('Batch close hour must be between 0.0 and 23.999'))

    def get_config(self):
        """Obtiene la configuración global (crea si no existe)."""
        config = self.search([], limit=1)
        if not config:
            config = self.create({})
        return config
