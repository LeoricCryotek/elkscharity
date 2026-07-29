# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# Second-pass backfill of attendance-sourced contributions.  The
# 19.0.7.0 migration ran on some databases without creating
# contributions — either because tasks lacked a GL category or
# because Odoo saw the version as already migrated.  This runs the
# backfill again with:
#   - defaults uncategorized tasks to 9999 (baked into the model
#     helper in 19.0.7.1)
#   - explicit x_elks_org_state='not_pushed' on new rows
#   - INFO logging so we can see counts in the Odoo log
# Idempotent — safe to run multiple times.
# === AI AGENT ===
# post-migrate runs after the ORM is loaded.  Uses the same
# _ensure_attendance_contribution helper the save hooks use, so any
# behavior tweaks stay in one place.
# ============================================================================
"""19.0.7.1 — Re-run attendance-contribution backfill with logging."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    Att = env["hr.attendance"].sudo()
    Contrib = env["elks.charity.contribution"].sudo()

    before = Contrib.search_count([("x_source", "=", "attendance")])
    historical = Att.search([
        ("x_charity_task_id", "!=", False),
        ("x_validated", "=", True),
        ("check_out", "!=", False),
    ])
    _logger.info(
        "elkscharity 19.0.7.1 backfill: %d validated charity "
        "attendance rows found, %d attendance-sourced contributions "
        "exist before backfill.",
        len(historical), before,
    )
    if historical:
        historical._ensure_attendance_contribution()

    after = Contrib.search_count([("x_source", "=", "attendance")])
    _logger.info(
        "elkscharity 19.0.7.1 backfill: %d attendance-sourced "
        "contributions after backfill (delta: %+d).",
        after, after - before,
    )

    # Re-alphabetize the app launcher on this upgrade too.
    from odoo.addons.elkscharity import alphabetize_app_menus
    alphabetize_app_menus(env)
