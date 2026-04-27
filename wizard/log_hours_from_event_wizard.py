# -*- coding: utf-8 -*-
"""Wizard that creates a timesheet line from a calendar event with
the charity category prefilled from the event."""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ElksCharityLogHoursWizard(models.TransientModel):
    _name = "elks.charity.log.hours.wizard"
    _description = "Log Charity Hours from Calendar Event"

    event_id = fields.Many2one("calendar.event", string="Source Event")
    task_id = fields.Many2one(
        "project.task", string="Charity Activity", required=True,
        domain="[('x_is_charity_activity', '=', True)]",
    )
    charity_category_id = fields.Many2one(
        "elks.charity.category",
        related="task_id.x_charity_category_id",
        readonly=True, string="Grand Lodge Category",
    )
    employee_id = fields.Many2one(
        "hr.employee", string="Volunteer / Employee",
        default=lambda self: self.env.user.employee_id,
        required=True,
    )
    is_helper = fields.Boolean(
        "Non-Elk Helper",
        help="Check if this is a non-Elk volunteer (family/friend/community).",
    )
    event_date = fields.Date(
        "Activity Date", default=fields.Date.context_today, required=True,
    )
    duration_hours = fields.Float(
        "Hours", required=True, default=0.0,
    )
    miles = fields.Float("Miles (Round Trip)", default=0.0)
    cash_value = fields.Monetary("Cash Donated", currency_field='currency_id')
    non_cash_value = fields.Monetary(
        "Non-Cash Value", currency_field='currency_id',
    )
    currency_id = fields.Many2one(
        "res.currency", default=lambda self: self.env.company.currency_id,
    )
    description = fields.Char("Description")
    notes = fields.Text("Notes")

    def action_create_timesheet(self):
        """Create the timesheet line(s)."""
        self.ensure_one()
        if not self.task_id:
            raise UserError(_("Please pick a charity activity task."))

        vals = {
            'project_id': self.task_id.project_id.id,
            'task_id': self.task_id.id,
            'employee_id': self.employee_id.id,
            'date': self.event_date,
            'unit_amount': self.duration_hours,
            'name': self.description or self.task_id.name,
            'x_is_helper': self.is_helper,
            'x_miles': self.miles,
            'x_cash_value': self.cash_value,
            'x_non_cash_value': self.non_cash_value,
            'x_charity_notes': self.notes,
        }

        line = self.env['account.analytic.line'].create(vals)

        line.message_post(
            body=(
                f"<strong>Hours logged from calendar event</strong><br/>"
                f"Event: {self.event_id.name if self.event_id else 'N/A'}<br/>"
                f"Activity: {self.task_id.name}<br/>"
                f"Hours: {self.duration_hours} / Miles: {self.miles}"
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        ) if hasattr(line, 'message_post') else None

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Hours Logged'),
                'message': _(
                    '%(h).1f hour(s) logged against %(task)s. '
                    'Secretary will validate before Grand Lodge report.'
                ) % {
                    'h': self.duration_hours,
                    'task': self.task_id.name,
                },
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
