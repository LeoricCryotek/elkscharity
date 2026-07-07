# -*- coding: utf-8 -*-
{
    "name": "Elks Charity — Volunteer Hours, Activities & Grand Lodge Report",
    "version": "19.0.2.15",
    "category": "Productivity",
    "summary": "Track volunteer hours and charitable activities per "
               "Grand Lodge categories (1001–9999). Uses Odoo Projects "
               "and Timesheets, with annual workbook PDF output.",
    "description": """
Elks Charity Workbook Module
=============================

Implements the Grand Lodge Charity Records Workbook (Code 511100) in
Odoo using the Project, Timesheets, and Calendar apps.

Architecture
------------
* Master project per lodge year: "Charity Work YYYY-YYYY"
* Each charitable activity is a task inside that project, tagged with
  one of the 24 Grand Lodge categories (1001 Youth Scholarships …
  9999 Categories Not Covered)
* Volunteers (and optionally employees) log hours via Odoo timesheets
  against these tasks
* Extra per-entry fields capture what the Grand Lodge report needs:
  miles, non-cash value, cash, head count, helper flag
* Hours require validation by a Secretary before counting
* Annual wrap-up wizard closes the year, generates the PDF report,
  and creates the next year's project
* Calendar events can be linked to a charity task so hours logged
  from the event prefill the category

Dependencies
------------
base, mail, project, hr_timesheet, calendar, elkscontacts, elksfrs
""",
    "author": "Danny Santiago",
    "website": "https://dannysantiago.info",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "project",
        "hr_timesheet",
        "hr_attendance",
        "calendar",
        "website",
        "portal",
        "elkscontacts",
        "elksfrs",
    ],
    "data": [
        "security/elkscharity_groups.xml",
        "security/ir.model.access.csv",
        "data/charity_category_data.xml",
        "data/initial_project_data.xml",
        "views/charity_category_views.xml",
        # quick_entry_wizard_views.xml must load BEFORE project_views.xml
        # and charity_dashboard_views.xml because both reference
        # action_charity_quick_entry_wizard via %(...)d.
        "wizard/quick_entry_wizard_views.xml",
        "views/project_views.xml",
        "views/timesheet_views.xml",
        "views/calendar_views.xml",
        "views/attendance_views.xml",
        "views/charity_hours_report_views.xml",
        "views/hr_employee_charity_views.xml",
        "views/res_partner_charity_views.xml",
        "views/charity_contribution_views.xml",
        "data/contribution_cron.xml",
        "wizard/yearly_wrapup_wizard_views.xml",
        "wizard/log_hours_from_event_wizard_views.xml",
        # timecard_report_wizard moved to elksattendance in 19.0.1.9
        "wizard/assign_charity_wizard_views.xml",
        "wizard/grand_lodge_report_wizard_views.xml",
        "wizard/charity_annual_report_wizard_views.xml",
        "report/charity_annual_report.xml",
        "report/grand_lodge_report.xml",
        "report/gl_entry_sheet_report.xml",
        "report/meeting_summary_report.xml",
        # report/timecard_report.xml moved to elksattendance in 19.0.1.9
        "views/charity_dashboard_views.xml",
        "views/charity_website_snippet.xml",
        "views/charity_portal_templates.xml",
        "views/elkscharity_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "elkscharity/static/src/scss/charity_dashboard.scss",
        ],
        "web.assets_frontend": [
            "elkscharity/static/src/scss/charity_website_snippet.scss",
            "elkscharity/static/src/js/charity_website_snippet.js",
        ],
    },
    "installable": True,
    "application": True,
}
