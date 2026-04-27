# -*- coding: utf-8 -*-
"""Links calendar events to a charity task so hours logged from an
event automatically carry the correct Grand Lodge category."""
from odoo import api, fields, models


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    x_charity_task_id = fields.Many2one(
        "project.task", string="Charity Activity",
        domain="[('x_is_charity_activity', '=', True)]",
        help="Link this calendar event to a charity activity task. "
             "Hours logged from this event will prefill the task and "
             "Grand Lodge category automatically.",
    )
    x_charity_category_id = fields.Many2one(
        "elks.charity.category",
        related="x_charity_task_id.x_charity_category_id",
        store=True, string="Grand Lodge Category", readonly=True,
    )
    x_charity_category_code = fields.Char(
        related="x_charity_category_id.code", store=True, readonly=True,
    )

    def action_log_charity_hours(self):
        """Open the Log Hours wizard prefilled from this event."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Log Volunteer Hours',
            'res_model': 'elks.charity.log.hours.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_event_id': self.id,
                'default_task_id': self.x_charity_task_id.id,
                'default_event_date': self.start.date() if self.start else False,
                'default_duration_hours': self.duration or 0.0,
            },
        }
