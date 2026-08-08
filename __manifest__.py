# -*- coding: utf-8 -*-
{
    "name": "Elks Charity — Volunteer Hours, Activities & Grand Lodge Report",
    "version": "19.0.7.19",
    "category": "Productivity",
    "summary": "Grand Lodge Charity Workbook in Odoo. Auto-generates "
               "elks.org submissions from attendance + Quick Entry, "
               "pushes them to elks.org via companion Chrome extension, "
               "prints the paper Data Collection Survey (Columns A–J).",
    "description": """
Elks Charity Workbook Module (v19.0.7.x)
=========================================

Full BPOE Grand Lodge Charity Records Workbook (Code 511100) in Odoo,
with automated pipeline from clock-in to elks.org submission.

What it does
------------
* One Charity Project per lodge year: "Charity Work YYYY-YYYY".
* Each activity = a task tagged with a Grand Lodge category
  (30 defaults, 1001 Youth Scholarships … 9999 Categories Not Covered).
* Volunteers clock in via attendance; hours auto-validate on save
  (no more manual Secretary click) and roll up to the category.
* Quick Entry wizard mirrors the paper Data Collection Survey
  (Columns A–J) — one form fills one contribution + optional per-
  member personal-record credit.
* When an attendance record matches a Quick Entry attribution,
  attendance wins: the personal-record line is deleted and the
  contribution's declared total is adjusted so nothing double-counts.
* Attendance auto-creates Confirmed contributions per (task, date)
  so every hour hits the elks.org push queue.

Reporting & submission
----------------------
* Dashboard: one widget-style card per (category × lodge year) with
  current-vs-prior-year deltas, right-sized to fit any viewport.
* BPOE Data Collection Survey PDF — paper-form-style, landscape,
  A–J columns, per-section subtotals + grand total + signature block.
  Bind directly to the Contributions list (Print dropdown) or run
  full-year from the Grand Lodge Report wizard.
* GL Website Entry Sheet PDF — one card per line for hand-entering
  into elks.org if the extension isn't available.
* Grand Lodge Charity Report + CSV export in the elks.org format.
* Public website snippet with "year-to-date totals" + per-category
  cards, per-category visibility toggle.

Elks.org auto-push
------------------
Companion Chrome extension in ``elkscharity/extension/`` submits
confirmed contributions to the Local Lodge Reporting form using the
Secretary's own logged-in browser session — bypassing elks.org's
bot detection that killed the earlier server-side Playwright path.
Zero credentials stored in Odoo; extension polls via per-user API
key (generated on Preferences → Elks.org Credentials). Dry-run mode
for test databases.

Setup helpers
-------------
* Missing Charity Category menu — batch-triage attendance rows that
  need a GL code assigned.
* Pending Elks.org Push menu — see exactly what will be submitted.
* Rebuild Attendance Contributions button (Configuration) — manually
  re-run the (task, date) contribution generator if something looks
  off.
* Auto-alphabetize app launcher on install/upgrade (mirrors the
  elkssecretary Alphabetize App Menus tool).

Dependencies
------------
base, mail, project, hr_timesheet, hr_attendance, calendar, website,
portal, elkscontacts, elksfrs
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
        "data/elks_org_config_params.xml",
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
        "views/res_users_elks_org_views.xml",
        "data/contribution_cron.xml",
        "wizard/yearly_wrapup_wizard_views.xml",
        "wizard/log_hours_from_event_wizard_views.xml",
        # timecard_report_wizard moved to elksattendance in 19.0.1.9
        "wizard/assign_charity_wizard_views.xml",
        "wizard/grand_lodge_report_wizard_views.xml",
        "wizard/charity_annual_report_wizard_views.xml",
        "wizard/elks_org_setup_wizard_views.xml",
        "wizard/attendance_import_wizard_views.xml",
        "wizard/bulk_enable_volunteer_wizard_views.xml",
        "report/charity_annual_report.xml",
        "report/grand_lodge_report.xml",
        "report/gl_entry_sheet_report.xml",
        "report/bpoe_data_collection_report.xml",
        "report/volunteer_records_form.xml",
        "report/meeting_summary_report.xml",
        # report/timecard_report.xml moved to elksattendance in 19.0.1.9
        "views/charity_dashboard_views.xml",
        "views/charity_website_snippet.xml",
        "views/charity_leaderboard_snippet.xml",
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
            "elkscharity/static/src/scss/charity_leaderboard_snippet.scss",
            "elkscharity/static/src/js/charity_leaderboard_snippet.js",
        ],
    },
    "installable": True,
    "application": True,
    # Runs once on first install — see __init__.py.  The same call
    # runs on upgrade via each version's post-migrate.py.
    "post_init_hook": "_post_init_hook",
}
