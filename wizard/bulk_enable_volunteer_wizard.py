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
    force_create_employee = fields.Boolean(
        "Force-create employee if none exists",
        default=True,
        help="elkscontacts' auto-sync only LINKS to existing "
             "employees when it finds a high-confidence match "
             "(exact email or existing work_contact link). It never "
             "creates new employees blindly. Turn this ON to make "
             "the wizard create a fresh employee record for every "
             "volunteer that still has none after the sync fires "
             "— tagged 'Volunteers' department and linked via "
             "work_contact_id so future syncs recognize the pairing.",
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
    # Chunk size for commit-per-batch.  Live server has a 60s CPU
    # limit per worker; each partner-flip + employee-sync is ~160ms,
    # so ~350 partners is the absolute ceiling and 250 is safe.
    # Committing every N keeps progress survivable across retries.
    _BATCH_SIZE = 25

    def _quiet_ctx(self, records):
        """Strip mail-tracking overhead — chatter posts on every
        employee create/write dominate the 60s CPU budget on the
        live server.  Bulk operations don't need audit trail per row.
        """
        return records.with_context(
            tracking_disable=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            mail_notrack=True,
            no_reset_password=True,
        )

    def action_apply(self):
        self.ensure_one()
        partners = self._get_target_partners()
        if not partners:
            raise UserError(_(
                "No partners in scope — nothing to do. Widen the "
                "scope or include already-volunteer members."
            ))

        # ---- Step 1: flip the volunteer flag on partners ----
        # Process in chunks with a commit after each so a worker
        # timeout doesn't wipe everything.  Chatter/tracking is
        # suppressed via _quiet_ctx — the elkscontacts sync hook
        # still fires but skips the mail.thread audit posts.
        # 19.0.7.28.
        flipped = self.env['res.partner']
        pending = partners.filtered(lambda p: not p.x_is_volunteer)
        for i in range(0, len(pending), self._BATCH_SIZE):
            chunk = pending[i:i + self._BATCH_SIZE]
            self._quiet_ctx(chunk).write({'x_is_volunteer': True})
            flipped |= chunk
            # Commit so a subsequent timeout doesn't undo this batch.
            self.env.cr.commit()

        # ---- Step 1b: force-create employees for stragglers ----
        # elkscontacts._sync_volunteer_employee only LINKS when it
        # finds a high-confidence match (exact email / work_contact).
        # For members without an email on file (or with mismatched
        # data) the sync silently skips creation and posts a chatter
        # note.  When force_create_employee is on, we finish the job:
        # create a fresh hr.employee for every partner still without
        # x_volunteer_employee_id, tagged Volunteers department and
        # work_contact_id linked so future syncs recognize the pair.
        force_created = self.env['hr.employee']
        if self.force_create_employee:
            # Refresh the recordset so post-sync state is visible.
            partners.invalidate_recordset(['x_volunteer_employee_id'])
            need_employee = partners.filtered(
                lambda p: not p.x_volunteer_employee_id
            )
            if need_employee:
                dept = need_employee[0]._get_or_create_volunteer_department()
                # Every force-created employee reports to whoever the
                # Volunteers department manager is (usually the
                # Volunteer Coordinator).  Falls back to no parent if
                # the department has no manager set — leaves the field
                # blank rather than crashing.  19.0.7.26.
                dept_manager_id = (dept.manager_id.id
                                   if dept.manager_id else False)
                Employee = self._quiet_ctx(self.env['hr.employee'].sudo())
                # res.partner in Odoo 19 no longer exposes a distinct
                # 'mobile' field on some installs (merged into phone).
                # Use hasattr() so this survives either shape without
                # crashing.  Prior code raised AttributeError on write.
                # Kiosk clock-in expects a 4-digit PIN
                # (hr.employee.pin).  Per the kiosk poster: default to
                # the last 4 digits of the volunteer's cell phone;
                # fallback "0000" when there's no phone on file.
                # 19.0.7.27.
                import re
                # Commit-per-chunk here too so a timeout keeps
                # whatever we already created.  19.0.7.28.
                for i in range(0, len(need_employee), self._BATCH_SIZE):
                    chunk = need_employee[i:i + self._BATCH_SIZE]
                    for p in chunk:
                        mobile = getattr(p, 'mobile', False)
                        phone_src = p.phone or mobile or ""
                        digits = re.sub(r'\D', '', str(phone_src))
                        pin = digits[-4:].zfill(4) if digits else '0000'
                        vals = {
                            'name': p.name or p.display_name or 'Volunteer',
                            'work_contact_id': p.id,
                            'work_email': p.email or False,
                            'work_phone': p.phone or mobile or False,
                            'department_id': dept.id,
                            'x_is_volunteer': True,
                            'pin': pin,
                        }
                        if dept_manager_id:
                            vals['parent_id'] = dept_manager_id
                            vals['coach_id'] = dept_manager_id
                        emp = Employee.create(vals)
                        self._quiet_ctx(p).sudo().write(
                            {'x_volunteer_employee_id': emp.id}
                        )
                        force_created |= emp
                    self.env.cr.commit()

        # ---- Step 1c: backfill PINs on employees that lack one ----
        # Kiosk requires hr.employee.pin (4 digits).  For every
        # employee tied to one of our partners, if pin is empty, set
        # it to last-4-of-cell (or "0000" fallback matching poster).
        # Never overwrites an existing pin — a Secretary may have
        # already set one manually.  19.0.7.27.
        import re
        pins_set = 0
        emps_by_partner = {
            e.work_contact_id.id: e
            for e in self.env['hr.employee'].sudo().search([
                ('work_contact_id', 'in', partners.ids),
            ]) if e.work_contact_id
        }
        for p in partners:
            emp = emps_by_partner.get(p.id)
            if not emp or emp.pin:
                continue
            mobile = getattr(p, 'mobile', False)
            phone_src = p.phone or mobile or ""
            digits = re.sub(r'\D', '', str(phone_src))
            pin = digits[-4:].zfill(4) if digits else '0000'
            emp.sudo().write({'pin': pin})
            pins_set += 1

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
        if self.force_create_employee and force_created:
            msg_lines.append(_(
                "Created %s new employee record(s) for volunteers "
                "without a prior match."
            ) % len(force_created))
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
