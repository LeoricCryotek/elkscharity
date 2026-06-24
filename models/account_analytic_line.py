# -*- coding: utf-8 -*-
"""Extends the timesheet line (``account.analytic.line``) with the
extra fields the Grand Lodge Charity Workbook requires: miles,
non-cash value, cash, helper flag, and Secretary validation."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    # Link to the charity category (via the task)
    x_charity_category_id = fields.Many2one(
        "elks.charity.category",
        related="task_id.x_charity_category_id", store=True, index=True,
        string="Charity Category",
    )
    x_is_charity_line = fields.Boolean(
        "Is Charity Hours",
        compute="_compute_is_charity_line", store=True, index=True,
    )

    # Non-Elks helper flag (Columns D / F / H on the workbook)
    x_is_helper = fields.Boolean(
        "Non-Elk Helper",
        help="Check if these hours were contributed by someone who is "
             "NOT an Elk (family, friend, community volunteer).  "
             "Elk hours go in columns C/E/G; Helper hours in D/F/H.",
    )

    # Grand Lodge workbook fields
    x_miles = fields.Float(
        "Miles (Round Trip)", default=0.0,
        help="Total miles driven for this activity, ROUND TRIP.\n\n"
             "Per the GL Workbook: enter the product of people × distance "
             "× round trip.  Example: 4 people drove 27½ miles each way → "
             "enter 4 × 27.5 × 2 ≈ 220 miles.  Round to WHOLE miles — no "
             "fractions, no decimals.",
    )
    x_non_cash_value = fields.Monetary(
        "Non-Cash Value", currency_field='currency_id',
        help="Fair market value of goods / materials donated, bought for, "
             "or expended on this activity.\n\n"
             "Per the GL Workbook: include door prizes, refreshments, "
             "supplies, postage, telephone charges, donated clothing or "
             "eyeglasses (use IRS valuation guidelines if unsure).  "
             "WHOLE DOLLARS only — no dollar signs, cents, or decimals.",
    )
    x_cash_value = fields.Monetary(
        "Cash Donated", currency_field='currency_id',
        help="Cash, check, or money order given or donated.\n\n"
             "Per the GL Workbook: for U.S. Savings Bonds, use the "
             "PURCHASE value, not maturity value.  WHOLE DOLLARS only — "
             "no dollar signs, cents, or decimals.",
    )

    # Grand Lodge Secretary validation
    x_validated = fields.Boolean(
        "Validated for GL Report", default=False,
        help="Lock this line as ready for inclusion in the annual "
             "Grand Lodge Charity Workbook.",
    )
    # Personal-record marker (added 19.0.2.11) — flags timesheet lines
    # created by the Quick Entry wizard purely so a member sees their
    # share of a bulk event in their personal "charity hours" history.
    # The corresponding charity.contribution already holds the bulk
    # totals for GL reporting; including these lines in the GL totals
    # would double-count.  All summing logic must filter these out.
    x_personal_record = fields.Boolean(
        "Personal Record Only", default=False, index=True, copy=False,
        help="Marks this line as personal-record-only: it shows on the "
             "member's individual charity history but is EXCLUDED from "
             "Grand Lodge totals and the Charity Dashboard.  Set by the "
             "Quick Entry wizard when bulk-attributing hours; the "
             "matching elks.charity.contribution holds the GL totals.",
    )
    # Back-link from a wizard-created personal-record line to the
    # contribution that holds the official bulk totals.
    x_source_contribution_id = fields.Many2one(
        "elks.charity.contribution", string="Source Bulk Contribution",
        readonly=True, ondelete="set null", index=True, copy=False,
    )
    x_validated_by = fields.Many2one(
        "res.users", string="Validated By", readonly=True, copy=False,
    )
    x_validated_on = fields.Date(
        "Validated On", readonly=True, copy=False,
    )
    x_charity_notes = fields.Text(
        "Charity Notes",
    )

    @api.depends("task_id", "task_id.project_id", "task_id.project_id.x_is_charity_parent")
    def _compute_is_charity_line(self):
        for rec in self:
            rec.x_is_charity_line = bool(
                rec.task_id
                and rec.task_id.project_id
                and rec.task_id.project_id.x_is_charity_parent
            )

    @api.onchange("task_id")
    def _onchange_task_prefill_charity(self):
        """When a task is selected, inherit its category code and event date."""
        if self.task_id and self.task_id.x_is_charity_activity:
            if self.task_id.x_event_date and not self.date:
                self.date = self.task_id.x_event_date

    @api.constrains("x_cash_value", "x_non_cash_value", "x_miles", "unit_amount")
    def _check_non_negative(self):
        for line in self:
            if (line.x_cash_value or 0) < 0 or (line.x_non_cash_value or 0) < 0:
                raise UserError(_("Cash and non-cash values cannot be negative."))
            if (line.x_miles or 0) < 0:
                raise UserError(_("Miles cannot be negative."))

    @api.constrains("task_id")
    def _check_project_not_closed(self):
        """Prevent posting hours to a closed charity project."""
        for line in self:
            proj = line.task_id.project_id
            if proj and proj.x_is_charity_parent and proj.x_is_closed:
                raise UserError(_(
                    "Cannot add hours to closed charity project "
                    "'%(project)s'.  The lodge year has been wrapped up."
                ) % {'project': proj.name})

    # -----------------------------------------------------------------
    # Validation actions
    # -----------------------------------------------------------------
    def action_validate_hours(self):
        """Secretary validates these hours — required before they show
        in the annual Grand Lodge report."""
        for line in self:
            if not line.x_is_charity_line:
                raise UserError(_(
                    "Line '%(name)s' is not a charity activity — "
                    "nothing to validate."
                ) % {'name': line.name or ''})
            if not line.task_id.x_charity_category_id:
                raise UserError(_(
                    "Task '%(task)s' has no Charity Category set. "
                    "Please pick a category on the task first."
                ) % {'task': line.task_id.name or ''})
            line.write({
                'x_validated': True,
                'x_validated_by': self.env.user.id,
                'x_validated_on': fields.Date.context_today(self),
            })

    def action_unvalidate_hours(self):
        self.write({
            'x_validated': False,
            'x_validated_by': False,
            'x_validated_on': False,
        })
