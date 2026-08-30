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

    # ─── Auto-volunteer for members (19.0.7.24) ─────────────────────
    # When a contact is flagged as an Elk member (x_is_member=True),
    # automatically flip x_is_volunteer=True so the elkscontacts
    # module's _sync_volunteer_employee hook creates/links their
    # hr.employee record.  Members can then clock in at the kiosk and
    # log volunteer hours without a Secretary manually enrolling them
    # via the volunteer wizard.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Only auto-set if the field exists on this install (i.e.
            # elkscontacts is present) and the record is a member.
            if 'x_is_member' in self._fields and vals.get('x_is_member') \
                    and 'x_is_volunteer' not in vals:
                vals['x_is_volunteer'] = True
        return super().create(vals_list)

    def write(self, vals):
        # Auto-set x_is_volunteer=True the moment x_is_member flips on
        # for existing records too — a Secretary marking someone as a
        # member later should immediately enable their volunteer clock-in.
        if 'x_is_member' in self._fields and vals.get('x_is_member') is True \
                and 'x_is_volunteer' not in vals:
            for rec in self:
                if not rec.x_is_volunteer:
                    vals = dict(vals, x_is_volunteer=True)
                    break
        return super().write(vals)

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
