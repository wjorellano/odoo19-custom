import logging

from odoo import models

_logger = logging.getLogger(__name__)

class PosSession(models.Model):
    _inherit = 'pos.session'

    def action_pos_session_close(self, *args, **kwargs):
        """
        Cierre de sesión de POS.

        NOTA: No ejecutamos batch close aquí.
        El batch close se ejecuta automáticamente mediante un CRON diariamente
        a la hora configurada en nuvei.module.config.batch_close_hour.
        Esto evita ejecutar batch close múltiples veces si hay muchas sesiones.
        """
        return super().action_pos_session_close(*args, **kwargs)
