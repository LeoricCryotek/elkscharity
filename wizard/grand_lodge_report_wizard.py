# -*- coding: utf-8 -*-
"""Grand Lodge Charitable Giving Report wizard.

Produces the annual report matching the Grand Lodge format:
- Grouped by GL section, then by category code
- Columns: Head Count (B), # Elks (C), # Helpers (D), Elks Hours (E),
  Helper Hours (F), Elks Miles (G), Helper Miles (H), Non-Cash (I), Cash (J)
- CSV export matching Grand Lodge column layout
- PDF report for lodge records

Filters: by event/task, by employee, by month, by lodge year.
"""
import base64
import csv
import io

from odoo import api, fields, models

from ..models.elks_charity_category import GL_SECTIONS


class GrandLodgeReportWizard(models.TransientModel):
    _name = 'elks.grand.lodge.report.wizard'
    _description = 'Grand Lodge Charitable Giving Report'

    date_from = fields.Date(
        'From', required=True,
        default=lambda self: fields.Date.today().replace(month=4, day=1),
    )
    date_to = fields.Date(
        'To', required=True,
        default=fields.Date.today,
    )
    project_ids = fields.Many2many(
        'project.project', string='Charity Projects',
        domain="[('x_is_charity_parent', '=', True)]",
        help='Filter by charity project/lodge year. Leave empty for all.',
    )
    task_ids = fields.Many2many(
        'project.task', string='Events / Tasks',
        domain="[('x_is_charity_activity', '=', True)]",
        help='Filter by specific events. Leave empty for all.',
    )
    employee_ids = fields.Many2many(
        'hr.employee', string='Employees / Volunteers',
        help='Filter by specific people. Leave empty for all.',
    )
    category_ids = fields.Many2many(
        'elks.charity.category', string='GL Categories',
        help='Filter by GL category. Leave empty for all.',
    )

    # CSV download fields
    state = fields.Selection(
        [('choose', 'Choose'), ('download', 'Download')],
        default='choose',
    )
    csv_file = fields.Binary('CSV File', readonly=True)
    csv_filename = fields.Char('Filename', readonly=True)

    def _get_report_data(self):
        """Build the Grand Lodge report data structure.

        Returns dict with 'sections' (list of section dicts) and 'grand_totals'.
        Each section contains categories, each category contains event rows.
        """
        # Find all charity tasks matching filters
        task_domain = [('x_is_charity_activity', '=', True)]
        if self.project_ids:
            task_domain.append(('project_id', 'in', self.project_ids.ids))
        if self.task_ids:
            task_domain.append(('id', 'in', self.task_ids.ids))
        if self.category_ids:
            task_domain.append(
                ('x_charity_category_id', 'in', self.category_ids.ids)
            )

        tasks = self.env['project.task'].search(
            task_domain, order='x_charity_category_code, x_event_date, name'
        )

        # Get attendance records in date range for these tasks
        att_domain = [
            ('x_charity_task_id', 'in', tasks.ids),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
            ('check_out', '!=', False),
        ]
        if self.employee_ids:
            att_domain.append(('employee_id', 'in', self.employee_ids.ids))

        attendances = self.env['hr.attendance'].search(att_domain)

        # Get timesheet lines in date range for these tasks
        ts_domain = [
            ('task_id', 'in', tasks.ids),
            ('date', '>=', self.date_from),
            ('date', '<=', self.date_to),
        ]
        if self.employee_ids:
            ts_domain.append(('employee_id', 'in', self.employee_ids.ids))

        timesheets = self.env['account.analytic.line'].search(ts_domain)

        # Build per-task aggregates
        task_data = {}
        for task in tasks:
            task_att = attendances.filtered(
                lambda a: a.x_charity_task_id.id == task.id
            )
            task_ts = timesheets.filtered(
                lambda l: l.task_id.id == task.id
            )

            # Dedupe: attendance wins over timesheet for same employee+date
            att_keys = set(
                (a.employee_id.id, a.check_in.date() if a.check_in else False)
                for a in task_att
            )
            ts_kept = task_ts.filtered(
                lambda l: (l.employee_id.id, l.date) not in att_keys
            )

            att_elks = task_att.filtered(lambda a: not a.x_is_helper)
            att_help = task_att.filtered('x_is_helper')
            ts_elks = ts_kept.filtered(lambda l: not l.x_is_helper)
            ts_help = ts_kept.filtered('x_is_helper')

            row = {
                'task': task,
                'event_date': task.x_event_date or self.date_from,
                'program_name': task.name,
                'category_code': task.x_charity_category_code or '',
                'head_count': task.x_head_count,
                'num_elks': len(set(
                    ts_elks.mapped('employee_id.id')
                    + att_elks.mapped('employee_id.id')
                )),
                'num_helpers': len(set(
                    ts_help.mapped('employee_id.id')
                    + att_help.mapped('employee_id.id')
                )),
                'elks_hours': (
                    sum(ts_elks.mapped('unit_amount'))
                    + sum(
                        a.x_charity_hours or a.worked_hours
                        for a in att_elks
                    )
                ),
                'helper_hours': (
                    sum(ts_help.mapped('unit_amount'))
                    + sum(
                        a.x_charity_hours or a.worked_hours
                        for a in att_help
                    )
                ),
                'elks_miles': (
                    sum(ts_elks.mapped('x_miles'))
                    + sum(att_elks.mapped('x_miles'))
                ),
                'helper_miles': (
                    sum(ts_help.mapped('x_miles'))
                    + sum(att_help.mapped('x_miles'))
                ),
                'non_cash': (
                    sum(ts_kept.mapped('x_non_cash_value'))
                    + sum(task_att.mapped('x_non_cash_value'))
                ),
                'cash': (
                    sum(ts_kept.mapped('x_cash_value'))
                    + sum(task_att.mapped('x_cash_value'))
                ),
            }

            # Only include tasks that have data in this period
            has_data = (
                row['num_elks'] or row['num_helpers']
                or row['head_count'] or row['cash'] or row['non_cash']
            )
            if has_data:
                task_data[task.id] = row

        # Organize by GL section → category
        sections = []
        grand_totals = {
            'head_count': 0, 'num_elks': 0, 'num_helpers': 0,
            'elks_hours': 0, 'helper_hours': 0,
            'elks_miles': 0, 'helper_miles': 0,
            'non_cash': 0.0, 'cash': 0.0,
        }

        for section_key, section_label in GL_SECTIONS:
            cats = self.env['elks.charity.category'].search([
                ('gl_section', '=', section_key),
            ], order='code')

            section_cats = []
            section_totals = {k: 0 for k in grand_totals}

            for cat in cats:
                cat_tasks = [
                    task_data[t.id] for t in tasks
                    if t.x_charity_category_id.id == cat.id
                    and t.id in task_data
                ]
                if not cat_tasks:
                    continue

                cat_totals = {k: 0 for k in grand_totals}
                for row in cat_tasks:
                    for k in cat_totals:
                        cat_totals[k] += row[k]

                section_cats.append({
                    'category': cat,
                    'events': cat_tasks,
                    'totals': cat_totals,
                })

                for k in section_totals:
                    section_totals[k] += cat_totals[k]

            if section_cats:
                sections.append({
                    'key': section_key,
                    'label': section_label,
                    'categories': section_cats,
                    'totals': section_totals,
                })
                for k in grand_totals:
                    grand_totals[k] += section_totals[k]

        # Collect tasks with no GL category, grouped by parent project
        uncat_by_project = {}
        for t in tasks:
            if t.x_charity_category_id or t.id not in task_data:
                continue
            proj_name = t.project_id.name if t.project_id else 'Other'
            proj_id = t.project_id.id if t.project_id else 0
            if proj_id not in uncat_by_project:
                uncat_by_project[proj_id] = {
                    'name': proj_name, 'rows': [],
                }
            uncat_by_project[proj_id]['rows'].append(task_data[t.id])

        for proj_id, proj_info in uncat_by_project.items():
            proj_totals = {k: 0 for k in grand_totals}
            for row in proj_info['rows']:
                for k in proj_totals:
                    proj_totals[k] += row[k]

            class _ProjCat:
                """Stub category so the template can read .display_name/.code."""
                display_name = "Needs GL Category"
                code = "----"
            sections.append({
                'key': f'proj_{proj_id}',
                'label': proj_info['name'],
                'categories': [{
                    'category': _ProjCat(),
                    'events': proj_info['rows'],
                    'totals': proj_totals,
                }],
                'totals': proj_totals,
            })
            for k in grand_totals:
                grand_totals[k] += proj_totals[k]

        return {
            'sections': sections,
            'grand_totals': grand_totals,
            'date_from': self.date_from,
            'date_to': self.date_to,
        }

    def action_print_report(self):
        """Preview the Grand Lodge report (HTML)."""
        self.ensure_one()
        report = self.env.ref(
            'elkscharity.action_report_grand_lodge'
        )
        return report.report_action(self)

    def action_download_pdf(self):
        """Download the Grand Lodge report as PDF."""
        self.ensure_one()
        report = self.env.ref(
            'elkscharity.action_report_grand_lodge_pdf'
        )
        return report.report_action(self)

    def action_export_csv(self):
        """Generate Grand Lodge format CSV."""
        self.ensure_one()
        result = self._get_report_data()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'GL Code', 'Category', 'Date', 'Program',
            'Head Count (B)', '# Elks (C)', '# Helpers (D)',
            'Elks Hours (E)', 'Helper Hours (F)',
            'Elks Miles (G)', 'Helper Miles (H)',
            'Non-Cash Value (I)', 'Cash (J)',
        ])

        for section in result['sections']:
            for cat_data in section['categories']:
                cat = cat_data['category']
                for event in cat_data['events']:
                    writer.writerow([
                        cat.code,
                        cat.name,
                        str(event['event_date']),
                        event['program_name'],
                        event['head_count'],
                        event['num_elks'],
                        event['num_helpers'],
                        f"{event['elks_hours']:.0f}",
                        f"{event['helper_hours']:.0f}",
                        f"{event['elks_miles']:.0f}",
                        f"{event['helper_miles']:.0f}",
                        f"{event['non_cash']:.2f}",
                        f"{event['cash']:.2f}",
                    ])

        csv_content = base64.b64encode(output.getvalue().encode('utf-8'))
        filename = (
            f"grand_lodge_charity_{self.date_from}_{self.date_to}.csv"
        )

        self.write({
            'state': 'download',
            'csv_file': csv_content,
            'csv_filename': filename,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
