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
        "Validated for GL Report", default=False, tracking=True,
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
            charity_records._invalidate_charity_tasks()
        return records

    def write(self, vals):
        # Collect tasks BEFORE write (in case x_charity_task_id changes)
        old_tasks = self.env['project.task']
        if self._CHARITY_TRIGGER_FIELDS & set(vals):
            old_tasks = self.mapped('x_charity_task_id')
        res = super().write(vals)
        if self._CHARITY_TRIGGER_FIELDS & set(vals):
            new_tasks = self.mapped('x_charity_task_id')
            self._invalidate_charity_tasks(old_tasks | new_tasks)
        return res

    def unlink(self):
        tasks = self.mapped('x_charity_task_id')
        res = super().unlink()
        if tasks:
            self._invalidate_charity_tasks(tasks)
        return res
