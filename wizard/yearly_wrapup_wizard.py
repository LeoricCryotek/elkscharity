# -*- coding: utf-8 -*-
"""Year-end wrap-up wizard.

Closes the current lodge-year charity project, prints the Grand Lodge
annual report for it, and creates the next year's parent project.
"""
import datetime

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ElksCharityYearlyWrapupWizard(models.TransientModel):
    _name = "elks.charity.yearly.wrapup.wizard"
    _description = "Charity Work Year-End Wrap-Up"

    closing_project_id = fields.Many2one(
        "project.project", string="Project to Close",
        domain="[('x_is_charity_parent', '=', True), ('x_is_closed', '=', False)]",
        required=True,
    )
    closing_lodge_year = fields.Selection(
        related="closing_project_id.x_lodge_year", readonly=True,
    )
    next_lodge_year = fields.Char(
        "Next Lodge Year",
        help="Defaults to the year after the closing project's lodge year.",
    )
    unvalidated_count = fields.Integer(
        "Unvalidated Hours Lines",
        compute="_compute_unvalidated",
    )

    @api.onchange("closing_project_id")
    def _onchange_closing_project(self):
        if not self.closing_project_id:
            return
        ly = self.closing_project_id.x_lodge_year or ''
        try:
            start = int(ly.split('-')[0])
            self.next_lodge_year = f"{start + 1}-{start + 2}"
        except (ValueError, IndexError):
            today = datetime.date.today()
            self.next_lodge_year = f"{today.year}-{today.year + 1}"

    @api.depends("closing_project_id")
    def _compute_unvalidated(self):
        for rec in self:
            if rec.closing_project_id:
                ts_count = self.env['account.analytic.line'].search_count([
                    ('task_id.project_id', '=', rec.closing_project_id.id),
                    ('x_validated', '=', False),
                ])
                # Also count attendance records tagged on this project's
                # tasks but not yet validated for the GL report.
                task_ids = rec.closing_project_id.task_ids.ids
                att_count = self.env['hr.attendance'].search_count([
                    ('x_charity_task_id', 'in', task_ids),
                    ('x_validated', '=', False),
                ]) if task_ids else 0
                rec.unvalidated_count = ts_count + att_count
            else:
                rec.unvalidated_count = 0

    def action_wrap_up(self):
        """Close the current project and create the next one."""
        self.ensure_one()
        if not self.closing_project_id:
            raise UserError(_("Please select the project to close."))

        if self.unvalidated_count:
            raise UserError(_(
                "There are %(n)d unvalidated hour lines on this "
                "project.  Validate or delete them first, or they "
                "won't appear on the Grand Lodge report."
            ) % {'n': self.unvalidated_count})

        # Mark the project closed
        self.closing_project_id.write({'x_is_closed': True})
        self.closing_project_id.message_post(
            body=(
                f"<strong>Lodge year closed</strong><br/>"
                f"Wrap-up performed by {self.env.user.name} on "
                f"{fields.Date.context_today(self)}."
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        # Create next year's project
        Project = self.env['project.project']
        next_project = Project.create_charity_parent_project(
            lodge_year=self.next_lodge_year,
        )

        # Return the Grand Lodge report action
        return self.env.ref(
            'elkscharity.action_report_charity_annual'
        ).report_action(self.closing_project_id)
