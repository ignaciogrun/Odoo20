from random import randint

from odoo import fields, models


class FileManagerTag(models.Model):
    _name = "file.manager.tag"
    _description = "File Tag"
    _order = "name"

    name = fields.Char(required=True, translate=True)
    color = fields.Integer(default=lambda self: randint(1, 11))

    _name_unique = models.Constraint("unique(name)", "A tag with this name already exists.")
