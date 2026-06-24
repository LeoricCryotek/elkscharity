# -*- coding: utf-8 -*-
"""Extend res.partner with charity contribution history for the Elks History tab."""
from odoo import api, fields, models


class ResPartnerCharity(models.Model):
    _inherit = "res.partner"

    x_charity_hours_ids = fields.One2many(
        'account.analytic.line', compute='_compute_charity_hours',
        string='Charity Contributions',
        help="Validated charity hours logged by this member.",
    )

    @api.depends('x_volunteer_employee_id')
    def _compute_charity_hours(self):
        """Charity hours for the member's personal history.

        Includes BOTH validated lines (timesheet entries from real
        events) AND personal-record lines created by the Quick Entry
        wizard.  Personal-record lines are excluded from GL totals
        (the bulk contribution carries those) but they DO show on the
        member's profile so they get credit for their participation
        in bulk-entered events.
        """
        AAL = self.env['account.analytic.line']
        for partner in self:
            emp = partner.x_volunteer_employee_id
            if emp:
                partner.x_charity_hours_ids = AAL.search([
                    ('employee_id', '=', emp.id),
                    ('x_is_charity_line', '=', True),
                ])
            else:
                partner.x_charity_hours_ids = AAL.browse()
