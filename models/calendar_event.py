# -*- coding: utf-8 -*-
"""Links calendar events to a charity task so hours logged from an
event automatically carry the correct Grand Lodge category. Also
optionally links the event to a recurring charity contribution
(e.g. weekly venue donation that recurs with the event)."""
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError


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

    # ── recurring contribution link ────────────────────────────────
    x_charity_contribution_id = fields.Many2one(
        "elks.charity.contribution",
        string="Recurring Contribution",
        domain="[('task_id', '=', x_charity_task_id)]",
        help="Optional: link this event to a recurring charity contribution "
             "such as a weekly venue donation, in-kind gift, or service. "
             "Use the dropdown's 'Create and Edit...' option to set up a new "
             "Recurring Contribution from here, or pick an existing one. "
             "The 'Create Next Occurrence' button below then operates on "
             "that contribution's recurrence.",
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

    def action_create_next_contribution_occurrence(self):
        """Proxy to the linked contribution's Create Next Occurrence action.

        Opens the new draft contribution in a form so the Secretary can
        adjust the numbers before confirming.
        """
        self.ensure_one()
        if not self.x_charity_contribution_id:
            raise UserError(_(
                "Pick a Recurring Contribution first, or use the "
                "dropdown's 'Create and Edit...' option to make one."
            ))
        return self.x_charity_contribution_id.action_duplicate_next()

    def action_open_charity_contribution(self):
        """Open the linked contribution in its own form for editing."""
        self.ensure_one()
        if not self.x_charity_contribution_id:
            raise UserError(_("No Recurring Contribution linked yet."))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Recurring Contribution'),
            'res_model': 'elks.charity.contribution',
            'res_id': self.x_charity_contribution_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def _cron_generate_event_driven_contributions(self):
        """For each calendar event in the recent past that links to a
        Recurring Contribution template, create a draft contribution
        dated to the event's day if one doesn't already exist.

        Driven by event date, not by the contribution's own frequency —
        so a weekly Tuesday event automatically produces one contribution
        draft per Tuesday, regardless of what the contribution's
        ``recurrence_frequency`` says.

        Window is the last 2 days (covers a 24h cron outage). Older
        events are NOT backfilled. The duplicate-check on
        (template_id, contribution_date) makes the cron idempotent.
        """
        now = fields.Datetime.now()
        window_start = now - timedelta(days=2)
        Contribution = self.env["elks.charity.contribution"]
        events = self.search([
            ("x_charity_contribution_id", "!=", False),
            ("start", ">=", window_start),
            ("start", "<=", now),
        ])
        for ev in events:
            tmpl = ev.x_charity_contribution_id
            if not ev.start:
                continue
            ev_date = ev.start.date()
            # Respect the contribution template's recurrence_end_date if set.
            if tmpl.recurrence_end_date and ev_date > tmpl.recurrence_end_date:
                continue
            existing = Contribution.search_count([
                ("template_id", "=", tmpl.id),
                ("contribution_date", "=", ev_date),
            ])
            if existing:
                continue
            tmpl.copy({
                "name": tmpl.name,
                "contribution_date": ev_date,
                "state": "draft",
                "template_id": tmpl.id,
                "is_recurring": False,
                "next_generation_date": False,
                "confirmed_by": False,
                "confirmed_date": False,
            })
