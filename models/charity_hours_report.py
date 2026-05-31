# -*- coding: utf-8 -*-
"""Unified read-only view of ALL charity hours — both timesheet entries
(``account.analytic.line``) and tagged attendance records
(``hr.attendance``) — in a single list.

Backed by a PostgreSQL VIEW so there is zero data duplication.
"""
from odoo import fields, models, tools


class ElksCharityHoursReport(models.Model):
    _name = "elks.charity.hours.report"
    _description = "Charity Hours (Unified)"
    _auto = False  # SQL view — no real table
    _order = "date desc, id desc"
    _rec_name = "description"

    source = fields.Selection([
        ('timesheet', 'Timesheet'),
        ('attendance', 'Attendance'),
    ], string="Source", readonly=True)
    date = fields.Date("Date", readonly=True)
    employee_id = fields.Many2one(
        "hr.employee", string="Employee", readonly=True,
    )
    project_id = fields.Many2one(
        "project.project", string="Project", readonly=True,
    )
    task_id = fields.Many2one(
        "project.task", string="Task", readonly=True,
    )
    charity_category_id = fields.Many2one(
        "elks.charity.category", string="GL Category", readonly=True,
    )
    description = fields.Char("Description", readonly=True)
    hours = fields.Float("Hours", readonly=True)
    is_helper = fields.Boolean("Non-Elk Helper", readonly=True)
    miles = fields.Float("Miles", readonly=True)
    cash_value = fields.Float("Cash Donated", readonly=True)
    non_cash_value = fields.Float("Non-Cash Value", readonly=True)
    validated = fields.Boolean("Validated for GL Report", readonly=True)
    lodge_year = fields.Char("Lodge Year", readonly=True)

    def init(self):
        """Create the SQL VIEW merging timesheets + attendance."""
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                -- Timesheet lines (account.analytic.line)
                SELECT
                    aal.id                              AS id,
                    'timesheet'                         AS source,
                    aal.date                            AS date,
                    aal.employee_id                     AS employee_id,
                    aal.project_id                      AS project_id,
                    aal.task_id                         AS task_id,
                    pt.x_charity_category_id            AS charity_category_id,
                    aal.name                            AS description,
                    aal.unit_amount                     AS hours,
                    COALESCE(aal.x_is_helper, FALSE)    AS is_helper,
                    COALESCE(aal.x_miles, 0)            AS miles,
                    COALESCE(aal.x_cash_value, 0)       AS cash_value,
                    COALESCE(aal.x_non_cash_value, 0)   AS non_cash_value,
                    COALESCE(aal.x_validated, FALSE)     AS validated,
                    pp.x_lodge_year                     AS lodge_year
                FROM account_analytic_line aal
                JOIN project_task pt    ON pt.id = aal.task_id
                JOIN project_project pp ON pp.id = aal.project_id
                WHERE pp.x_is_charity_parent = TRUE

                UNION ALL

                -- Attendance records tagged as charity
                SELECT
                    -- Offset IDs so they never collide with timesheet IDs
                    1000000000 + ha.id                  AS id,
                    'attendance'                        AS source,
                    ha.check_in::date                   AS date,
                    ha.employee_id                      AS employee_id,
                    pt.project_id                       AS project_id,
                    ha.x_charity_task_id                AS task_id,
                    pt.x_charity_category_id            AS charity_category_id,
                    pt.name                             AS description,
                    CASE
                        WHEN COALESCE(ha.x_charity_hours, 0) > 0
                            THEN ha.x_charity_hours
                        WHEN ha.check_in IS NOT NULL
                             AND ha.check_out IS NOT NULL
                            THEN EXTRACT(EPOCH FROM
                                    (ha.check_out - ha.check_in))
                                 / 3600.0
                        ELSE COALESCE(ha.worked_hours, 0)
                    END                                 AS hours,
                    COALESCE(ha.x_is_helper, FALSE)     AS is_helper,
                    COALESCE(ha.x_miles, 0)             AS miles,
                    COALESCE(ha.x_cash_value, 0)        AS cash_value,
                    COALESCE(ha.x_non_cash_value, 0)    AS non_cash_value,
                    COALESCE(ha.x_validated, FALSE)      AS validated,
                    pp.x_lodge_year                     AS lodge_year
                FROM hr_attendance ha
                JOIN project_task pt    ON pt.id = ha.x_charity_task_id
                JOIN project_project pp ON pp.id = pt.project_id
                WHERE ha.x_charity_task_id IS NOT NULL
                  AND pp.x_is_charity_parent = TRUE
            )
        """ % self._table)
