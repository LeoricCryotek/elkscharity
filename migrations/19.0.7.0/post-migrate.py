# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# One-time backfill on upgrade to 19.0.7.0.  Historical validated
# charity attendance never triggered contribution auto-creation
# (the hook is new).  Sweep every (task, date) bucket that has
# validated attendance and no existing contribution, and create a
# Confirmed attendance-sourced contribution so those hours flow into
# the elks.org push queue.
# === AI AGENT ===
# Runs the same _ensure_attendance_contribution logic the save hook
# runs, but for the historical dataset in one pass.  Uses the ORM so
# the create() side effects (contribution numbering, chatter, etc.)
# fire correctly.
# ============================================================================
"""19.0.7.0 — Backfill contributions for historical charity attendance."""


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})
    Att = env["hr.attendance"].sudo()
    historical = Att.search([
        ("x_charity_task_id", "!=", False),
        ("x_validated", "=", True),
        ("check_out", "!=", False),
    ])
    if historical:
        historical._ensure_attendance_contribution()
