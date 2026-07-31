# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# 19.0.7.12: Fix UTC-date bug that shifted evening attendance one day
# forward on elks.org.  Odoo stores check_in as naive UTC; earlier
# versions called check_in.date() directly, so a Danny Santiago
# clock-in at 5:37 PM Pacific (Jun 3) → stored as 00:37 UTC (Jun 4)
# → contribution.event_date=Jun 4 → elks.org Program Date=Jun 4.
#
# Fix in hr_attendance.py now converts check_in to the company
# timezone before calling .date().  This migration re-runs the
# attendance→contribution backfill so historical rows recompute
# their event_date with the corrected logic.  Any duplicate
# contributions created by the shift will be merged by the standard
# _ensure_attendance_contribution helper (matches on task+date).
#
# Old shifted contributions that are now UNBACKED (no attendance on
# their date after the tz fix) are deleted so they stop appearing in
# the elks.org Pending Push queue and don't get re-submitted with
# the wrong date.
# === AI AGENT ===
# Idempotent — safe to run multiple times.  Uses SUPERUSER_ID so it
# bypasses record-level ACLs on the contribution model.
# ============================================================================
"""19.0.7.12 — Rebuild attendance contributions with tz-correct dates."""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    from odoo import api, SUPERUSER_ID
    env = api.Environment(cr, SUPERUSER_ID, {})

    Att = env["hr.attendance"].sudo()
    Contrib = env["elks.charity.contribution"].sudo()

    # Snapshot the "before" state so the log tells us what changed.
    before_count = Contrib.search_count([("x_source", "=", "attendance")])
    before_pending_push = Contrib.search_count([
        ("x_source", "=", "attendance"),
        ("x_elks_org_state", "=", "not_pushed"),
    ])
    _logger.info(
        "elkscharity 19.0.7.12 tz-fix: %d attendance-sourced "
        "contributions before rebuild (%d pending push).",
        before_count, before_pending_push,
    )

    # Re-run the backfill.  With the tz fix in place,
    # _ensure_attendance_contribution will:
    #   - MATCH existing contributions on the correct local date and
    #     leave them alone (no-op).
    #   - CREATE new contributions on the corrected date if the
    #     previous shifted date has no attendance backing.
    #   - UPDATE hours/counts on any existing contribution whose date
    #     is now different.
    historical = Att.search([
        ("x_charity_task_id", "!=", False),
        ("x_validated", "=", True),
        ("check_out", "!=", False),
    ])
    if historical:
        _logger.info(
            "elkscharity 19.0.7.12 tz-fix: re-running "
            "_ensure_attendance_contribution on %d validated "
            "charity attendance rows.",
            len(historical),
        )
        historical._ensure_attendance_contribution()

    # Find orphan contributions: x_source='attendance' but no
    # matching attendance exists for their (task_id, event_date) in
    # the CORRECTED local date.  Those are the shifted duplicates
    # that need to go away.  Iterate contributions, ask "is there
    # attendance for me?", delete if not.
    from odoo.addons.elkscharity.models.hr_attendance import _local_date
    orphans = env["elks.charity.contribution"]
    for contrib in Contrib.search([("x_source", "=", "attendance")]):
        matching_atts = Att.search([
            ("x_charity_task_id", "=", contrib.task_id.id),
            ("x_validated", "=", True),
            ("check_out", "!=", False),
        ]).filtered(
            lambda a: a.check_in and
                      _local_date(env, a.check_in) == contrib.contribution_date
        )
        if not matching_atts:
            orphans |= contrib

    if orphans:
        # Only unlink if they haven't been pushed to elks.org yet
        # (safer — the Sync will clean up any already-pushed rows via
        # Purge Duplicates on the extension side).
        deletable = orphans.filtered(
            lambda c: c.x_elks_org_state != "pushed"
        )
        undeleteable = orphans - deletable
        _logger.info(
            "elkscharity 19.0.7.12 tz-fix: found %d orphan "
            "attendance contributions (no matching attendance on "
            "their date after tz fix).  Deleting %d that are not "
            "yet pushed to elks.org; leaving %d already-pushed rows "
            "for the extension's Purge step to clean up.",
            len(orphans), len(deletable), len(undeleteable),
        )
        deletable.unlink()

    after_count = Contrib.search_count([("x_source", "=", "attendance")])
    _logger.info(
        "elkscharity 19.0.7.12 tz-fix: %d attendance-sourced "
        "contributions after rebuild (delta: %+d).",
        after_count, after_count - before_count,
    )

    # Re-alphabetize the app launcher on this upgrade too.
    from odoo.addons.elkscharity import alphabetize_app_menus
    alphabetize_app_menus(env)
