# -*- coding: utf-8 -*-
"""Charity Contribution — non-attendance charitable activity entries.

Allows the Secretary or Charity Manager to record contributions that
don't involve individual volunteer attendance: in-kind donations,
venue use, cash gifts, supply donations, etc.  Contributions link to a
charity task (project.task) and roll up into the Grand Lodge report
alongside attendance-based data.

Recurring contributions (e.g. weekly venue donation for a church)
are supported.  A template record generates future draft entries
that must be confirmed by the Secretary, who can adjust the numbers
before confirming.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


CONTRIBUTION_TYPE = [
    ('in_kind', 'In-Kind Donation'),
    ('venue', 'Venue / Facility Use'),
    ('cash', 'Cash Donation'),
    ('supplies', 'Supplies / Materials'),
    ('service', 'Professional Service'),
    ('other', 'Other'),
]

FREQUENCY_SELECTION = [
    ('weekly', 'Weekly'),
    ('biweekly', 'Every 2 Weeks'),
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
]


class ElksCharityContribution(models.Model):
    """Non-attendance charitable activity entry."""

    _name = "elks.charity.contribution"
    _description = "Charity Contribution"
    _order = "contribution_date desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(
        "Description", required=True, tracking=True,
        help="Brief description of the contribution "
             "(e.g. 'Sunday venue donation to First Baptist Church').",
    )
    contribution_date = fields.Date(
        "Date", required=True,
        default=fields.Date.context_today, index=True, tracking=True,
    )
    contribution_type = fields.Selection(
        CONTRIBUTION_TYPE, string="Type", required=True,
        default='in_kind', tracking=True,
    )

    # ── link to charity task ─────────────────────────────────────
    task_id = fields.Many2one(
        "project.task", string="Charity Activity",
        required=True, index=True, tracking=True,
        domain="[('x_is_charity_activity', '=', True)]",
        help="The charity task this contribution counts toward.",
    )
    project_id = fields.Many2one(
        related="task_id.project_id", store=True, string="Charity Project",
    )
    charity_category_id = fields.Many2one(
        related="task_id.x_charity_category_id", store=True,
        string="GL Category",
    )
    charity_section = fields.Selection(
        related="task_id.x_charity_section", store=True,
        string="GL Section",
    )
    lodge_year = fields.Selection(
        related="task_id.x_lodge_year", store=True, index=True,
    )

    # ── contribution values ──────────────────────────────────────
    cash_value = fields.Monetary(
        "Cash Value", currency_field='currency_id', tracking=True,
        help="Cash amount donated.",
    )
    non_cash_value = fields.Monetary(
        "Non-Cash Value", currency_field='currency_id', tracking=True,
        help="Fair market value of in-kind goods, venue, services, etc.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    # ── people counts ────────────────────────────────────────────
    head_count = fields.Integer(
        "People Served",
        help="Number of people who benefited from this contribution.",
    )
    elks_count = fields.Integer(
        "# Elks Involved",
        help="Number of Elks members involved (without logging hours).",
    )
    helper_count = fields.Integer(
        "# Helpers Involved",
        help="Number of non-Elk helpers involved.",
    )

    # ── recipient ────────────────────────────────────────────────
    recipient_org = fields.Char(
        "Recipient Organization",
        help="External organization receiving this contribution.",
    )

    # ── notes ────────────────────────────────────────────────────
    notes = fields.Text("Notes")

    # ── state ────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, index=True,
       help="Draft: awaiting review.\n"
            "Confirmed: verified and included in reports.\n"
            "Cancelled: excluded from reports.",
    )

    # ── recurrence ───────────────────────────────────────────────
    is_recurring = fields.Boolean(
        "Recurring", tracking=True,
        help="Mark as a repeating contribution. Future entries will be "
             "auto-generated as drafts for review.",
    )
    recurrence_frequency = fields.Selection(
        FREQUENCY_SELECTION, string="Frequency",
    )
    recurrence_end_date = fields.Date(
        "Recurrence Ends",
        help="Stop generating entries after this date. "
             "Leave blank to continue indefinitely.",
    )
    template_id = fields.Many2one(
        "elks.charity.contribution", string="Generated From",
        readonly=True, ondelete="set null", index=True,
        help="The recurring template that created this entry.",
    )
    generated_ids = fields.One2many(
        "elks.charity.contribution", "template_id",
        string="Generated Entries",
    )
    next_generation_date = fields.Date(
        "Next Generation Date",
        help="Date for the next auto-generated entry.",
    )

    # ── who ──────────────────────────────────────────────────────
    submitted_by = fields.Many2one(
        "res.users", string="Submitted By",
        default=lambda self: self.env.user, tracking=True,
    )
    confirmed_by = fields.Many2one(
        "res.users", string="Confirmed By",
        readonly=True, tracking=True,
    )
    confirmed_date = fields.Datetime("Confirmed At", readonly=True)

    # ── actions ──────────────────────────────────────────────────
    def action_confirm(self):
        """Secretary confirms the contribution entry."""
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft contributions can be confirmed."))
            rec.write({
                'state': 'confirmed',
                'confirmed_by': self.env.user.id,
                'confirmed_date': fields.Datetime.now(),
            })
            rec.message_post(
                body=_(
                    "<strong>Contribution Confirmed</strong><br/>"
                    "%(name)s — $%(cash).2f cash, $%(noncash).2f non-cash.<br/>"
                    "Confirmed by %(who)s.",
                    name=rec.name,
                    cash=rec.cash_value,
                    noncash=rec.non_cash_value,
                    who=self.env.user.name,
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

    def action_cancel(self):
        """Cancel the contribution."""
        for rec in self:
            if rec.state == 'confirmed':
                raise UserError(_(
                    "Cannot cancel a confirmed contribution. "
                    "Reset to draft first if you need to cancel."
                ))
            rec.state = 'cancelled'

    def action_reset_draft(self):
        """Reset to draft for re-review."""
        for rec in self:
            rec.write({
                'state': 'draft',
                'confirmed_by': False,
                'confirmed_date': False,
            })

    def action_duplicate_next(self):
        """Quick-duplicate this entry for the next occurrence date."""
        self.ensure_one()
        next_date = self._get_next_date(self.contribution_date)
        new = self.copy({
            'contribution_date': next_date,
            'state': 'draft',
            'template_id': self.id if self.is_recurring else False,
            'confirmed_by': False,
            'confirmed_date': False,
            'is_recurring': False,
            'next_generation_date': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': new.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ── recurrence helpers ───────────────────────────────────────
    def _get_next_date(self, from_date):
        """Calculate the next date based on recurrence frequency."""
        freq = self.recurrence_frequency
        if freq == 'weekly':
            return from_date + timedelta(weeks=1)
        elif freq == 'biweekly':
            return from_date + timedelta(weeks=2)
        elif freq == 'monthly':
            return from_date + relativedelta(months=1)
        elif freq == 'quarterly':
            return from_date + relativedelta(months=3)
        # Default: one week
        return from_date + timedelta(weeks=1)

    @api.model
    def _cron_generate_recurring(self):
        """Cron: generate next draft entries for all active recurring templates.

        Runs daily.  For each recurring contribution whose
        next_generation_date <= today, creates a new draft entry and
        advances the next_generation_date.
        """
        today = fields.Date.context_today(self)
        templates = self.search([
            ('is_recurring', '=', True),
            ('state', '=', 'confirmed'),
            ('next_generation_date', '<=', today),
            '|',
            ('recurrence_end_date', '=', False),
            ('recurrence_end_date', '>=', today),
        ])
        for tmpl in templates:
            gen_date = tmpl.next_generation_date
            # Generate entries up to today (in case cron missed days)
            while gen_date and gen_date <= today:
                # Check end date
                if tmpl.recurrence_end_date and gen_date > tmpl.recurrence_end_date:
                    break
                # Don't create if one already exists for this date
                existing = self.search_count([
                    ('template_id', '=', tmpl.id),
                    ('contribution_date', '=', gen_date),
                ])
                if not existing:
                    tmpl.copy({
                        'contribution_date': gen_date,
                        'state': 'draft',
                        'template_id': tmpl.id,
                        'is_recurring': False,
                        'next_generation_date': False,
                        'confirmed_by': False,
                        'confirmed_date': False,
                    })
                gen_date = tmpl._get_next_date(gen_date)

            # Advance the template's next_generation_date
            tmpl.next_generation_date = gen_date

    @api.onchange('is_recurring', 'recurrence_frequency', 'contribution_date')
    def _onchange_recurrence(self):
        """Set next generation date when recurrence is configured."""
        if self.is_recurring and self.recurrence_frequency and self.contribution_date:
            self.next_generation_date = self._get_next_date(
                self.contribution_date
            )
        elif not self.is_recurring:
            self.next_generation_date = False

    # ── validation ───────────────────────────────────────────────
    @api.constrains('cash_value', 'non_cash_value')
    def _check_values(self):
        for rec in self:
            if rec.cash_value < 0 or rec.non_cash_value < 0:
                raise ValidationError(_(
                    "Contribution values cannot be negative."
                ))

    @api.constrains('contribution_date', 'recurrence_end_date')
    def _check_dates(self):
        for rec in self:
            if (rec.recurrence_end_date
                    and rec.contribution_date
                    and rec.recurrence_end_date < rec.contribution_date):
                raise ValidationError(_(
                    "Recurrence end date cannot be before the contribution date."
                ))
