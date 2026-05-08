# -*- coding: utf-8 -*-
"""Wizard to select a charity project before printing the Annual Report.

Without this wizard the menu item triggers ir.actions.report directly
with no records, producing a blank page.  The wizard lets the Secretary
pick a lodge-year project and then passes it as ``docs`` to the
existing ``report_charity_annual`` QWeb template.
"""
from odoo import fields, models


class CharityAnnualReportWizard(models.TransientModel):
    _name = 'elks.charity.annual.report.wizard'
    _description = 'Charity Annual Report Wizard'

    project_id = fields.Many2one(
        'project.project',
        string='Charity Project',
        required=True,
        domain="[('x_is_charity_parent', '=', True)]",
        help='Select the lodge-year charity project to report on.',
    )

    def action_preview(self):
        """Render the annual report as an HTML preview."""
        self.ensure_one()
        report = self.env.ref('elkscharity.action_report_charity_annual')
        return report.report_action(self.project_id)

    def action_download_pdf(self):
        """Download the annual report as a PDF."""
        self.ensure_one()
        report = self.env.ref('elkscharity.action_report_charity_annual_pdf')
        return report.report_action(self.project_id)
