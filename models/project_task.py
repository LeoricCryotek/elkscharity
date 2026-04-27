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
        help="Total number of people served by this activity.",
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

    # Roll-ups from validated timesheet lines
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

    @api.depends("project_id", "project_id.x_is_charity_parent")
    def _compute_is_charity(self):
        for rec in self:
            rec.x_is_charity_activity = bool(
                rec.project_id and rec.project_id.x_is_charity_parent
            )

    @api.depends(
        "timesheet_ids",
        "timesheet_ids.x_validated",
        "timesheet_ids.x_is_helper",
        "timesheet_ids.unit_amount",
        "timesheet_ids.x_miles",
        "timesheet_ids.x_cash_value",
        "timesheet_ids.x_non_cash_value",
    )
    def _compute_totals(self):
        """Roll up validated hours / miles / dollars from BOTH:
          * account.analytic.line (timesheets) and
          * hr.attendance records tagged with this task.

        Dedupe rule: if both a validated timesheet line AND a validated
        attendance record exist for the same employee + same date + this
        task, the attendance record wins (employees who clock in
        produce attendance; the timesheet line is treated as a duplicate).
        """
        Attendance = self.env.get('hr.attendance')
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

            # Use x_charity_hours when set, else fall back to worked_hours
            rec.x_elks_hours = (
                sum(ts_elks.mapped('unit_amount'))
                + sum(a.x_charity_hours or a.worked_hours for a in att_elks)
            )
            rec.x_helper_hours = (
                sum(ts_help.mapped('unit_amount'))
                + sum(a.x_charity_hours or a.worked_hours for a in att_help)
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
            ))
            rec.x_helper_count = len(set(
                ts_help.mapped('employee_id.id')
                + att_help.mapped('employee_id.id')
            ))
            rec.x_cash_total = (
                sum(ts_kept.mapped('x_cash_value'))
                + sum(att_validated.mapped('x_cash_value'))
            )
            rec.x_non_cash_total = (
                sum(ts_kept.mapped('x_non_cash_value'))
                + sum(att_validated.mapped('x_non_cash_value'))
            )
