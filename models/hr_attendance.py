# -*- coding: utf-8 -*-
"""Lets an existing employee Attendance record (clock-in / clock-out)
be tagged as charity work, so the same hours that show up on the
employee's time card also count toward the Grand Lodge Charity Report.

IMPORTANT design note
---------------------
This module **never modifies an existing attendance record's
check_in / check_out / worked_hours fields**.  All charity-related
fields are pure metadata sitting alongside the original attendance
data, so installing or upgrading this module cannot change any
employee's recorded work time.

The Grand Lodge report and the project task roll-ups pull from BOTH
sources (timesheet lines AND tagged attendance records), de-duped by
employee + date + task so a clock-in tagged as charity does not get
double-counted if a timesheet line also exists for the same activity.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    # --- Charity tagging (all optional, all metadata) ---
    x_charity_task_id = fields.Many2one(
        "project.task", string="Charity Activity",
        domain="[('x_is_charity_activity', '=', True)]",
        help="Tag this attendance entry as time spent on a specific "
             "charity activity.  Counts toward the Grand Lodge Charity "
             "Report once validated.  Leave empty for non-charity work.",
    )
    x_charity_hours = fields.Float(
        "Charity Hours",
        help="Hours to count toward the charity report.  Defaults to the "
             "raw clock-in → clock-out duration when a Charity Activity "
             "is selected; override if only part of the shift was charity "
             "work.\n\n"
             "Per the GL Workbook: enter the TOTAL elapsed time for the "
             "group.  If 6 Elks worked as a group for 3 hours, total Elk "
             "Hours is 18 (6 × 3).  Round to WHOLE hours — no fractions "
             "or decimals.",
    )
    x_charity_category_id = fields.Many2one(
        "elks.charity.category",
        related="x_charity_task_id.x_charity_category_id",
        store=True, readonly=True, string="GL Category",
    )
    x_is_charity_attendance = fields.Boolean(
        "Is Charity Time", compute="_compute_is_charity",
        store=True, index=True,
    )
    x_is_helper = fields.Boolean(
        "Non-Elk Helper", default=False,
        help="Check if these are non-Elk volunteer hours (rare for "
             "employees, common when guests clock in for a charity event).",
    )
    x_miles = fields.Float(
        "Miles (Round Trip)", default=0.0,
        help="Total miles driven for this activity, ROUND TRIP.  Per the "
             "GL Workbook: enter people × distance × round trip.  Round "
             "to WHOLE miles — no fractions or decimals.",
    )
    x_cash_value = fields.Monetary(
        "Cash Donated", currency_field='x_currency_id',
        help="Cash, check, or money order donated.  Per the GL Workbook: "
             "WHOLE DOLLARS only — no dollar signs, cents, or decimals.  "
             "For U.S. Savings Bonds, use purchase value, not maturity.",
    )
    x_non_cash_value = fields.Monetary(
        "Non-Cash Value", currency_field='x_currency_id',
        help="Fair market value of donated goods (refreshments, supplies, "
             "door prizes, postage, donated clothing, etc.).  Per the GL "
             "Workbook: WHOLE DOLLARS only.  Use IRS valuation guidelines "
             "for used items if unsure.",
    )
    x_charity_notes = fields.Text("Charity Notes")
    x_validated = fields.Boolean(
        "Validated for GL Report", default=True, tracking=True,
        help="Auto-set to True the moment a charity task is attached "
             "(19.0.6.5+).  Was manually gated in earlier versions "
             "when the Secretary had to check off 'yes, I entered "
             "this on elks.org' — the Chrome extension makes that "
             "redundant.  Uncheck manually only if you need to "
             "exclude this row from GL totals for some reason.",
    )
    x_validated_by = fields.Many2one(
        "res.users", string="Validated By", readonly=True, copy=False,
    )
    x_validated_on = fields.Date(
        "Validated On", readonly=True, copy=False,
    )
    x_currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    @api.depends("x_charity_task_id")
    def _compute_is_charity(self):
        for rec in self:
            rec.x_is_charity_attendance = bool(rec.x_charity_task_id)

    @api.constrains("x_charity_task_id")
    def _check_charity_project_not_closed(self):
        """Prevent tagging attendance to a closed charity project."""
        for rec in self:
            if not rec.x_charity_task_id:
                continue
            proj = rec.x_charity_task_id.project_id
            if proj and proj.x_is_charity_parent and proj.x_is_closed:
                raise UserError(_(
                    "Cannot tag attendance to closed charity project "
                    "'%(project)s'.  The lodge year has been wrapped up."
                ) % {'project': proj.name})

    @api.onchange("x_charity_task_id", "check_in", "check_out")
    def _onchange_charity_task(self):
        """Default charity hours to the raw clock-in → clock-out duration
        when a charity task is selected.

        We deliberately do NOT use ``worked_hours`` here — Odoo computes
        that field by subtracting unpaid breaks defined in the employee's
        resource calendar (e.g. lunch).  Volunteers don't take unpaid
        lunch breaks while volunteering, so 11 AM → 3 PM should be 4 h
        on the GL report, not 3 h.
        """
        if self.x_charity_task_id and not self.x_charity_hours:
            self.x_charity_hours = self._compute_raw_charity_hours()

    def _compute_raw_charity_hours(self):
        """Return the raw clock-in to clock-out duration in hours,
        with no break/lunch deduction.  Used as the default for
        ``x_charity_hours`` and as the fallback when that field is
        not explicitly set on a validated attendance record."""
        self.ensure_one()
        if self.check_in and self.check_out:
            delta = self.check_out - self.check_in
            return delta.total_seconds() / 3600.0
        return 0.0

    def action_reset_charity_hours_to_raw(self):
        """Recompute ``x_charity_hours`` from clock-in/clock-out for
        every record in self.  Use this to fix historical attendance
        rows that got the old worked_hours-based default."""
        for rec in self:
            if rec.x_charity_task_id:
                rec.x_charity_hours = rec._compute_raw_charity_hours()

    # ------------------------------------------------------------------
    # Validation actions
    # ------------------------------------------------------------------
    def action_validate_charity_attendance(self):
        for rec in self:
            if not rec.x_charity_task_id:
                continue
            rec.write({
                'x_validated': True,
                'x_validated_by': self.env.user.id,
                'x_validated_on': fields.Date.context_today(self),
            })

    def action_unvalidate_charity_attendance(self):
        self.write({
            'x_validated': False,
            'x_validated_by': False,
            'x_validated_on': False,
        })

    # ------------------------------------------------------------------
    # Invalidate charity task totals when attendance records change
    # ------------------------------------------------------------------
    _CHARITY_TRIGGER_FIELDS = {
        'x_charity_task_id', 'x_charity_hours', 'x_validated',
        'x_is_helper', 'x_miles', 'x_cash_value', 'x_non_cash_value',
        'worked_hours', 'check_in', 'check_out',
    }

    def _invalidate_charity_tasks(self, task_ids=None):
        """Force recompute of totals on linked charity tasks."""
        if task_ids is None:
            task_ids = self.mapped('x_charity_task_id')
        if task_ids:
            task_ids._compute_totals()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        charity_records = records.filtered('x_charity_task_id')
        if charity_records:
            # Auto-validate every charity attendance row on create so
            # hours count immediately.  Prior versions gated on a
            # manual click — see field docstring.
            charity_records._auto_validate_charity()
            # Attendance is authoritative — remove any Quick Entry
            # personal-record line that covers the same person + task
            # + date, and reduce the parent contribution's declared
            # hours by the removed PR share.  See _reconcile_pr_lines.
            charity_records._reconcile_pr_lines()
            charity_records._invalidate_charity_tasks()
        return records

    def write(self, vals):
        # Collect tasks BEFORE write (in case x_charity_task_id changes)
        old_tasks = self.env['project.task']
        if self._CHARITY_TRIGGER_FIELDS & set(vals):
            old_tasks = self.mapped('x_charity_task_id')
        res = super().write(vals)
        # If someone JUST attached a charity task to an existing
        # attendance row, auto-validate the same as we do on create.
        if 'x_charity_task_id' in vals and vals.get('x_charity_task_id'):
            newly_tagged = self.filtered('x_charity_task_id')
            newly_tagged._auto_validate_charity()
            newly_tagged._reconcile_pr_lines()
        elif ('check_in' in vals or 'x_charity_hours' in vals) and (
                self._CHARITY_TRIGGER_FIELDS & set(vals)):
            # Attendance date or hours changed — re-check for PR
            # matches on the (possibly new) date.
            self.filtered('x_charity_task_id')._reconcile_pr_lines()
        if self._CHARITY_TRIGGER_FIELDS & set(vals):
            new_tasks = self.mapped('x_charity_task_id')
            self._invalidate_charity_tasks(old_tasks | new_tasks)
        return res

    def _auto_validate_charity(self):
        """Flip x_validated=True on charity attendance rows that aren't
        already validated.  Idempotent."""
        needs_flip = self.filtered(
            lambda r: r.x_charity_task_id and not r.x_validated
        )
        if not needs_flip:
            return
        needs_flip.sudo().write({
            'x_validated': True,
            'x_validated_by': self.env.user.id,
            'x_validated_on': fields.Date.context_today(self),
        })

    def _reconcile_pr_lines(self):
        """Attendance is the authoritative source of truth for a
        person's charity hours.  When new charity attendance arrives
        for (employee, task, date) that already has a Quick Entry
        personal-record line for the same tuple:

          1. Delete the personal-record analytic line — it's stale
             the moment the real clock-in shows up.
          2. Reduce the parent contribution's declared elks/helper
             hours + miles by the removed PR share so the total that
             pushes to elks.org reflects reality (attendance replaces
             the estimate).
          3. Chatter-log the swap on the contribution so the audit
             trail is preserved.

        Idempotent: subsequent attendance edits find no PR line and
        no-op.  If attendance is later deleted, the contribution is
        NOT restored — the Secretary can re-attribute manually if
        that's really wanted.
        """
        AAL = self.env["account.analytic.line"].sudo()
        for att in self:
            if not att.x_charity_task_id or not att.check_in:
                continue
            att_date = att.check_in.date()
            matching_prs = AAL.search([
                ("task_id", "=", att.x_charity_task_id.id),
                ("employee_id", "=", att.employee_id.id),
                ("date", "=", att_date),
                ("x_personal_record", "=", True),
            ])
            if not matching_prs:
                continue
            # Group PR lines by parent contribution so we adjust each
            # contribution once with the total-of-shared-lines.
            contribs_touched = {}
            for pr in matching_prs:
                contrib = pr.x_source_contribution_id
                key = contrib.id if contrib else 0
                if key not in contribs_touched:
                    contribs_touched[key] = {
                        "contrib": contrib,
                        "elk_hours": 0.0,
                        "help_hours": 0.0,
                        "elk_miles": 0.0,
                        "help_miles": 0.0,
                        "employees": set(),
                    }
                bucket = contribs_touched[key]
                if pr.x_is_helper:
                    bucket["help_hours"] += pr.unit_amount
                    bucket["help_miles"] += pr.x_miles or 0.0
                else:
                    bucket["elk_hours"] += pr.unit_amount
                    bucket["elk_miles"] += pr.x_miles or 0.0
                bucket["employees"].add(pr.employee_id.name)

            for info in contribs_touched.values():
                contrib = info["contrib"]
                if contrib:
                    contrib.sudo().write({
                        "elks_hours": max(
                            0.0, (contrib.elks_hours or 0.0)
                                 - info["elk_hours"]),
                        "helper_hours": max(
                            0.0, (contrib.helper_hours or 0.0)
                                 - info["help_hours"]),
                        "elks_miles": max(
                            0.0, (contrib.elks_miles or 0.0)
                                 - info["elk_miles"]),
                        "helper_miles": max(
                            0.0, (contrib.helper_miles or 0.0)
                                 - info["help_miles"]),
                    })
                    contrib.message_post(
                        body=_(
                            "<strong>Attendance override applied.</strong>"
                            "<br/>%(who)s clocked in for this "
                            "activity on %(date)s — their personal-"
                            "record share (Elk hrs %(eh).2f · miles "
                            "%(em).2f · Helper hrs %(hh).2f · miles "
                            "%(hm).2f) has been removed from this "
                            "contribution's declared totals and "
                            "replaced by the actual attendance.",
                            who=", ".join(sorted(info["employees"])),
                            date=att_date,
                            eh=info["elk_hours"],
                            em=info["elk_miles"],
                            hh=info["help_hours"],
                            hm=info["help_miles"],
                        ),
                        subtype_xmlid="mail.mt_note",
                    )
            matching_prs.unlink()

    def unlink(self):
        tasks = self.mapped('x_charity_task_id')
        res = super().unlink()
        if tasks:
            self._invalidate_charity_tasks(tasks)
        return res
