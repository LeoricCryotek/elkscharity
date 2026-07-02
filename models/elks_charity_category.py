# -*- coding: utf-8 -*-
"""Grand Lodge charity / community service categories.

These are the 28 numbered categories across 11 sections from the
Grand Lodge Charity Records Workbook (Code 511100) that each lodge
reports annually.
"""
from odoo import api, fields, models


GL_SECTIONS = [
    ('youth', 'Youth Programs'),
    ('athletics', 'Youth Athletics'),
    ('special', 'Special Programs'),
    ('patriotic', 'Patriotic'),
    ('veterans', "Veterans' Service"),
    ('community', 'Community Service'),
    ('public', 'Public Service'),
    ('enf', 'Elks National Foundation'),
    ('drug', 'Drug Awareness'),
    ('auxiliary', 'Auxiliary Organizations'),
    ('other', 'Other / Not Covered'),
]


class ElksCharityCategory(models.Model):
    _name = "elks.charity.category"
    _description = "Grand Lodge Charity Reporting Category"
    _order = "code"
    _rec_name = "display_name"

    code = fields.Char(
        "GL Code", required=True, index=True,
        help="Four-digit Grand Lodge category code (e.g. 1001).",
    )
    name = fields.Char("Category Name", required=True, index=True)
    gl_section = fields.Selection(
        GL_SECTIONS, string="Section", required=True, index=True,
    )
    description = fields.Text("What to report here")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    x_auto_create_task = fields.Boolean(
        "Auto-Create Each Year", default=False,
        help="When checked, a charity activity (project task) for this "
             "category is automatically created whenever a new lodge-year "
             "charity project is started.  Use for recurring programmes "
             "like ENF, Hoop Shoot, Drug Awareness, etc.",
    )
    x_show_on_website = fields.Boolean(
        "Show on Website", default=True,
        help="Toggle whether this category's card appears in the public "
             "\"Elks Charity Impact\" website snippet.  Uncheck to hide "
             "categories the lodge isn't actively pursuing this year.  "
             "The internal Charity Dashboard is not affected.",
    )

    display_name = fields.Char(
        compute="_compute_display_name", store=True,
    )

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else (rec.name or '')
