# -*- coding: utf-8 -*-
"""Bulk-enable the Volunteer flag on Elks members AND set their
default charity activity in one pass.

Two problems solved together:

  1. Every Elk should be flagged as a volunteer for hour-tracking
     purposes, but flipping x_is_volunteer one contact at a time is
     tedious with 500+ members. This wizard flips it in bulk.

  2. When x_is_volunteer flips True on res.partner, the elkscontacts
     module auto-syncs an hr.employee record (via
     _sync_volunteer_employee). The default charity activity lives on
     that employee record (hr.employee.x_default_charity_task_id) —
     so setting it "at the same time" means: flip partners → let the
     employee sync fire → write the default charity onto the freshly
     synced employees. This wizard does that whole sequence.

Two scopes:
  * All Elks members  — every partner where x_is_member=True.
  * Selected partners — whoever the Secretary picked in the list view
                       (active_ids passed via context).
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ElksBulkEnableVolunteerWizard(models.TransientModel):
    _name = "elks.bulk.enable.volunteer.wizard"
    _description = "Bulk Enable Volunteer + Default Charity"

    scope = fields.Selection([
        ('all_elks_members', 'All Elks Members'),
        ('selected', 'Records selected in the list view'),
    ], string="Apply to", required=True, default='all_elks_members',
       help="Choose whether to flip every Elks member's volunteer "
            "flag, or just the partners you had selected in the "
            "Contacts list view when you launched this wizard.")

    default_charity_task_id = fields.Many2one(
        "project.task", string="Default Charity Activity",
        domain="[('x_is_charity_activity', '=', True)]",
        help="Optional. When set, this activity becomes each "
             "volunteer's default — new attendance check-ins "
             "auto-tag to it. Leave blank to only flip the "
             "volunteer flag without touching charity defaults.",
    )
    override_existing_default = fields.Boolean(
        "Overwrite existing default charity",
        default=False,
        help="Off (default): only fill in the default charity for "
             "volunteers who don't already have one — respects any "
             "hand-picked defaults. On: overwrite every volunteer's "
             "default with the choice above.",
    )
    include_already_volunteer = fields.Boolean(
        "Include members already flagged as Volunteer",
        default=True,
        help="On (default): still visit already-volunteer members so "
             "we can set their default charity. Off: only touch "
             "members whose volunteer flag is currently OFF.",
    )

    partner_count = fields.Integer(
        "Partners in scope", compute="_compute_partner_count",
    )

    # ------------------------------------------------------------------
    # Scope resolution + counts
    # ------------------------------------------------------------------
    def _get_target_partners(self):
        """Return the res.partner recordset the wizard will touch."""
        self.ensure_one()
        Partner = self.env['res.partner']
        if self.scope == 'selected':
            ids = self.env.context.get('active_ids') or []
            partners = Partner.browse(ids).filtered(
                lambda p: p.x_is_member
            )
        else:
            partners = Partner.search([('x_is_member', '=', True)])
        if not self.include_already_volunteer:
            partners = partners.filtered(lambda p: not p.x_is_volunteer)
        return partners

    @api.depends('scope', 'include_already_volunteer')
    def _compute_partner_count(self):
        for wiz in self:
            try:
                wiz.partner_count = len(wiz._get_target_partners())
            except Exception:
                wiz.partner_count = 0

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------
    def action_apply(self):
        self.ensure_one()
        partners = self._get_target_partners()
        if not partners:
            raise UserError(_(
                "No partners in scope — nothing to do. Widen the "
                "scope or include already-volunteer members."
            ))

        # ---- Step 1: flip the volunteer flag on partners ----
        # We write in one go so the elkscontacts write hook + employee
        # sync fires cleanly for the whole batch. Partners already
        # True are unaffected by writing True again.
        flipped = partners.filtered(lambda p: not p.x_is_volunteer)
        if flipped:
            flipped.write({'x_is_volunteer': True})

        # ---- Step 2: set the default charity on their employees ----
        # x_default_charity_task_id lives on hr.employee. res.partner
        # → hr.employee is linked via work_contact_id. After the flip
        # above, elkscontacts._sync_volunteer_employee has ensured an
        # hr.employee exists for every partner in the batch (creating
        # it if missing).
        employees_touched = self.env['hr.employee']
        default_id = self.default_charity_task_id.id if \
            self.default_charity_task_id else False
        if default_id:
            employees = self.env['hr.employee'].search([
                ('work_contact_id', 'in', partners.ids),
            ])
            for emp in employees:
                # Skip if we're not allowed to overwrite
                if (emp.x_default_charity_task_id
                        and not self.override_existing_default):
                    continue
                if emp.x_default_charity_task_id.id == default_id:
                    continue
                emp.write({'x_default_charity_task_id': default_id})
                employees_touched |= emp

        # ---- Feedback ----
        msg_lines = [
            _("Volunteer flag set on %s partner(s).") % len(flipped),
        ]
        if default_id:
            msg_lines.append(_(
                "Default charity activity set on %s employee(s)."
            ) % len(employees_touched))
        elif self.default_charity_task_id:
            # (defensive — shouldn't reach here)
            msg_lines.append(_(
                "No employees needed a default charity update."
            ))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Bulk volunteer update complete"),
                'message': " ".join(msg_lines),
                'type': 'success',
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
