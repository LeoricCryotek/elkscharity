# -*- coding: utf-8 -*-
"""Extends ``project.task`` so each task can represent a single
charitable activity tagged with a Grand Lodge category."""
from odoo import api, fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

    x_charity_category_id = fields.Many2one(
        "elks.charity.category", string="Charity Category",
        index=True, tracking=True,
        help="Grand Lodge reporting category this activity rolls up to.",
    )
    x_charity_category_code = fields.Char(
        related="x_charity_category_id.code", store=True,
    )
    x_charity_section = fields.Selection(
        related="x_charity_category_id.gl_section", store=True,
        string="GL Section",
    )
    x_head_count = fields.Integer(
        "Participants (Head Count)",
        help="Total number of people who BENEFITED from or were SERVED "
             "by this activity.  This is Column B on the GL Workbook.\n\n"
             "Example: a Bicycle Safety event with 217 children → enter "
             "217.  Distinct from # Elks / # Helpers, which are the "
             "VOLUNTEERS who put the event on (Columns C and D).",
    )
    x_total_head_count = fields.Integer(
        "Total Head Count", compute="_compute_totals",
        help="Manual head count + confirmed contribution head counts.",
    )
    x_recipient_org = fields.Char(
        "Recipient Organization",
        help="External beneficiary, if any (e.g. 'Idaho Food Bank').",
    )
    x_event_date = fields.Date(
        "Event Date",
        help="Date the activity took place (used on the annual report).",
    )
    x_charity_notes = fields.Text(
        "Charity Notes",
        help="Optional notes for the Secretary's records.",
    )
    x_lodge_year = fields.Selection(
        related="project_id.x_lodge_year", store=True, index=True,
    )
    x_is_charity_activity = fields.Boolean(
        "Is Charity Activity", compute="_compute_is_charity", store=True,
        index=True,
    )

    # Roll-ups from validated timesheet lines + contributions
    x_elks_hours = fields.Float(
        "Elks Hours", compute="_compute_totals",
    )
    x_helper_hours = fields.Float(
        "Helper Hours", compute="_compute_totals",
    )
    x_elks_miles = fields.Float(
        "Elks Miles", compute="_compute_totals",
    )
    x_helper_miles = fields.Float(
        "Helper Miles", compute="_compute_totals",
    )
    x_elks_count = fields.Integer(
        "# Elks", compute="_compute_totals",
    )
    x_helper_count = fields.Integer(
        "# Helpers", compute="_compute_totals",
    )
    x_cash_total = fields.Monetary(
        "Cash Contributions", compute="_compute_totals",
        currency_field='x_currency_id',
    )
    x_non_cash_total = fields.Monetary(
        "Non-Cash Value", compute="_compute_totals",
        currency_field='x_currency_id',
    )
    x_currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    # Contributions (non-attendance entries)
    x_contribution_ids = fields.One2many(
        "elks.charity.contribution", "task_id",
        string="Contributions",
    )

    @api.depends("project_id", "project_id.x_is_charity_parent")
    def _compute_is_charity(self):
        for rec in self:
            rec.x_is_charity_activity = bool(
                rec.project_id and rec.project_id.x_is_charity_parent
            )

    @api.depends(
        "x_head_count",
        "timesheet_ids",
        "timesheet_ids.x_validated",
        "timesheet_ids.x_is_helper",
        "timesheet_ids.unit_amount",
        "timesheet_ids.x_miles",
        "timesheet_ids.x_cash_value",
        "timesheet_ids.x_non_cash_value",
        "x_contribution_ids",
        "x_contribution_ids.state",
        "x_contribution_ids.cash_value",
        "x_contribution_ids.non_cash_value",
        "x_contribution_ids.elks_count",
        "x_contribution_ids.helper_count",
        "x_contribution_ids.head_count",
    )
    def _compute_totals(self):
        """Roll up validated hours / miles / dollars from THREE sources:
          * account.analytic.line (timesheets)
          * hr.attendance records tagged with this task
          * elks.charity.contribution entries (confirmed)

        Dedupe rule for attendance vs timesheets: if both a validated
        timesheet line AND a validated attendance record exist for the
        same employee + same date + this task, the attendance record
        wins (employees who clock in produce attendance; the timesheet
        line is treated as a duplicate).

        Contributions are additive — they don't overlap with attendance
        since they represent non-attendance entries (venue, in-kind, etc.).
        """
        Attendance = self.env.get('hr.attendance')
        Contribution = self.env.get('elks.charity.contribution')
        for rec in self:
            # --- timesheet lines (validated) ---
            ts_validated = rec.timesheet_ids.filtered('x_validated')

            # --- attendance records tagged to this task (validated) ---
            att_validated = self.env['hr.attendance']
            if Attendance is not None and rec.id:
                att_validated = Attendance.search([
                    ('x_charity_task_id', '=', rec.id),
                    ('x_validated', '=', True),
                ])

            # Build dedupe set keyed on (employee_id, date) for attendance
            att_keys = set(
                (a.employee_id.id, a.check_in.date() if a.check_in else False)
                for a in att_validated
            )

            # Filter timesheet lines: drop any line whose (employee, date)
            # already appears in attendance for this task
            ts_kept = ts_validated.filtered(
                lambda l: (l.employee_id.id, l.date) not in att_keys
            )

            # Combine into Elks vs Helper buckets
            ts_elks = ts_kept.filtered(lambda l: not l.x_is_helper)
            ts_help = ts_kept.filtered('x_is_helper')
            att_elks = att_validated.filtered(lambda a: not a.x_is_helper)
            att_help = att_validated.filtered('x_is_helper')

            # --- confirmed contributions ---
            contrib_cash = 0.0
            contrib_non_cash = 0.0
            contrib_elks = 0
            contrib_helpers = 0
            contrib_heads = 0
            if Contribution is not None and rec.id:
                contribs = Contribution.sudo().search([
                    ('task_id', '=', rec.id),
                    ('state', '=', 'confirmed'),
                ])
                contrib_cash = sum(contribs.mapped('cash_value'))
                contrib_non_cash = sum(contribs.mapped('non_cash_value'))
                contrib_elks = sum(contribs.mapped('elks_count'))
                contrib_helpers = sum(contribs.mapped('helper_count'))
                contrib_heads = sum(contribs.mapped('head_count'))

            # Use x_charity_hours when set; otherwise fall back to the
            # raw clock-in → clock-out duration (NOT worked_hours, which
            # subtracts unpaid breaks defined in the resource calendar
            # and would under-count volunteer time).
            def _att_hours(a):
                if a.x_charity_hours:
                    return a.x_charity_hours
                if a.check_in and a.check_out:
                    return (a.check_out - a.check_in).total_seconds() / 3600.0
                return 0.0
            rec.x_elks_hours = (
                sum(ts_elks.mapped('unit_amount'))
                + sum(_att_hours(a) for a in att_elks)
            )
            rec.x_helper_hours = (
                sum(ts_help.mapped('unit_amount'))
                + sum(_att_hours(a) for a in att_help)
            )
            rec.x_elks_miles = (
                sum(ts_elks.mapped('x_miles'))
                + sum(att_elks.mapped('x_miles'))
            )
            rec.x_helper_miles = (
                sum(ts_help.mapped('x_miles'))
                + sum(att_help.mapped('x_miles'))
            )
            rec.x_elks_count = len(set(
                ts_elks.mapped('employee_id.id')
                + att_elks.mapped('employee_id.id')
            )) + contrib_elks
            rec.x_helper_count = len(set(
                ts_help.mapped('employee_id.id')
                + att_help.mapped('employee_id.id')
            )) + contrib_helpers
            rec.x_cash_total = (
                sum(ts_kept.mapped('x_cash_value'))
                + sum(att_validated.mapped('x_cash_value'))
                + contrib_cash
            )
            rec.x_non_cash_total = (
                sum(ts_kept.mapped('x_non_cash_value'))
                + sum(att_validated.mapped('x_non_cash_value'))
                + contrib_non_cash
            )
            # Total head count = manual task-level + confirmed contributions
            rec.x_total_head_count = rec.x_head_count + contrib_heads

    # ------------------------------------------------------------------
    # Auto-categorize tasks in charity-parent projects that were created
    # without a Charity Category set.  Falls back to 9999 "Categories
    # Not Covered" so every charity activity rolls up somewhere on the
    # dashboard instead of falling off the map.
    # ------------------------------------------------------------------
    @api.model
    def _default_uncovered_charity_category_id(self):
        cat = self.env.ref(
            "elkscharity.cat_9999", raise_if_not_found=False
        )
        return cat.id if cat else False

    def _ensure_charity_category(self):
        """If the task lives in a charity-parent project and has no
        category, default it to 9999.  Idempotent."""
        fallback = self._default_uncovered_charity_category_id()
        if not fallback:
            return
        for rec in self:
            if rec.x_charity_category_id:
                continue
            proj = rec.project_id
            if proj and proj.x_is_charity_parent:
                rec.x_charity_category_id = fallback

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._ensure_charity_category()
        return records

    def write(self, vals):
        res = super().write(vals)
        # If either the project link or the category was touched, make
        # sure a charity-parent task still has a category.
        if "project_id" in vals or "x_charity_category_id" in vals:
            self._ensure_charity_category()
        return res
