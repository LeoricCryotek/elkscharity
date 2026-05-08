# -*- coding: utf-8 -*-
"""Extends ``project.project`` to mark a project as the lodge's
annual Charity Work container."""
import datetime

from odoo import api, fields, models


def _lodge_year_selections(self):
    """Generate selection list of lodge years (10 back, 5 forward).

    Lodge year runs April 1 – March 31.
    """
    today = datetime.date.today()
    current_start = today.year if today.month >= 4 else today.year - 1
    years = []
    for y in range(current_start - 10, current_start + 6):
        label = f"{y}-{y + 1}"
        years.append((label, label))
    return years


def _current_lodge_year():
    today = datetime.date.today()
    if today.month >= 4:
        return f"{today.year}-{today.year + 1}"
    return f"{today.year - 1}-{today.year}"


class ProjectProject(models.Model):
    _inherit = "project.project"

    x_is_charity_parent = fields.Boolean(
        "Charity Parent Project", default=False, index=True,
        help="Mark this project as the Charity Work parent for a lodge year. "
             "Its tasks (activities) become selectable as charity activities "
             "on attendance records and appear in all charity reports. "
             "Each lodge year should have exactly one.",
    )
    x_lodge_year = fields.Selection(
        selection=_lodge_year_selections,
        string="Lodge Year", index=True,
        help="YYYY-YYYY, e.g. 2025-2026.  Automatically set when "
             "x_is_charity_parent is true.",
    )
    x_is_closed = fields.Boolean(
        "Closed", default=False,
        help="Set when the lodge year has ended and the annual "
             "report has been submitted.  Closed charity projects "
             "are read-only.",
    )
    x_total_hours = fields.Float(
        "Total Hours", compute="_compute_charity_totals",
    )
    x_total_miles = fields.Float(
        "Total Miles", compute="_compute_charity_totals",
    )
    x_total_cash = fields.Monetary(
        "Total Cash", compute="_compute_charity_totals",
        currency_field='currency_id',
    )
    x_total_non_cash = fields.Monetary(
        "Total Non-Cash Value", compute="_compute_charity_totals",
        currency_field='currency_id',
    )
    x_total_participants = fields.Integer(
        "People Served", compute="_compute_charity_totals",
    )

    @api.depends("task_ids", "task_ids.x_total_head_count",
                 "task_ids.x_elks_hours",
                 "task_ids.x_helper_hours",
                 "task_ids.x_elks_miles",
                 "task_ids.x_helper_miles",
                 "task_ids.x_cash_total",
                 "task_ids.x_non_cash_total")
    def _compute_charity_totals(self):
        """Roll up from project tasks (which themselves merge timesheet
        + attendance sources)."""
        for rec in self:
            tasks = rec.task_ids
            rec.x_total_hours = (
                sum(tasks.mapped('x_elks_hours'))
                + sum(tasks.mapped('x_helper_hours'))
            )
            rec.x_total_miles = (
                sum(tasks.mapped('x_elks_miles'))
                + sum(tasks.mapped('x_helper_miles'))
            )
            rec.x_total_cash = sum(tasks.mapped('x_cash_total'))
            rec.x_total_non_cash = sum(tasks.mapped('x_non_cash_total'))
            rec.x_total_participants = sum(tasks.mapped('x_total_head_count'))

    @api.model
    def create_charity_parent_project(self, lodge_year=None):
        """Create the master Charity Work project for a lodge year.

        Returns the new (or existing) project record.  Automatically
        creates starter tasks for every GL category flagged with
        ``x_auto_create_task`` (ENF, Hoop Shoot, Drug Awareness, etc.)
        so the Secretary starts each year with core charities ready.
        """
        lodge_year = lodge_year or _current_lodge_year()
        existing = self.search([
            ('x_is_charity_parent', '=', True),
            ('x_lodge_year', '=', lodge_year),
        ], limit=1)
        if existing:
            return existing
        project = self.create({
            'name': f"Charity Work {lodge_year}",
            'x_is_charity_parent': True,
            'x_lodge_year': lodge_year,
            'allow_timesheets': True,
        })

        # Auto-create tasks for core/recurring charities
        Category = self.env['elks.charity.category']
        core_cats = Category.search([
            ('x_auto_create_task', '=', True),
            ('active', '=', True),
        ], order='sequence')
        Task = self.env['project.task']
        for cat in core_cats:
            Task.create({
                'name': cat.name,
                'project_id': project.id,
                'x_charity_category_id': cat.id,
            })

        task_count = len(core_cats)
        project.message_post(
            body=(
                f"<strong>Charity Work parent project created</strong><br/>"
                f"Lodge Year: {lodge_year}<br/>"
                f"{task_count} core charity activities auto-created.<br/>"
                f"Add more activity tasks for one-off events as needed."
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )
        return project

    def action_open_charity_dashboard(self):
        """Open the charity activities for this project using the
        dedicated charity list view, grouped by category."""
        self.ensure_one()
        tree_id = self.env.ref(
            'elkscharity.view_task_tree_charity_activities'
        ).id
        return {
            'type': 'ir.actions.act_window',
            'name': f"Charity Activities — {self.name}",
            'res_model': 'project.task',
            'view_mode': 'list,pivot,graph,form',
            'views': [
                (tree_id, 'list'),
                (False, 'pivot'),
                (False, 'graph'),
                (False, 'form'),
            ],
            'domain': [('project_id', '=', self.id)],
            'context': {
                'default_project_id': self.id,
                'search_default_group_category': 1,
            },
        }
