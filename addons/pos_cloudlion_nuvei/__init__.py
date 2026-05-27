# -*- coding: utf-8 -*-
# Módulo POS CloudLion x Nuvei
# Solo importamos models. No usamos controllers porque
# la comunicación JS→Python se hace via ORM calls directos
# al modelo pos.payment.method (estándar Odoo 19).
from . import models
