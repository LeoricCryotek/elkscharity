# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# One-time backfill on upgrade to 19.0.6.5.  Charity attendance no
# longer requires manual validation — the Chrome extension makes the
# "did I enter this on elks.org" gate redundant.  Every existing
# charity-tagged attendance row gets x_validated=True so those hours
# start counting toward the roll-up and dashboard immediately.
# === AI AGENT ===
# post-migrate.py runs AFTER the module upgrade.  Uses raw SQL so we
# don't fire the write() hook (which would call _invalidate_charity_tasks
# for every row and be slow for a large backfill).  Also stamps
# x_validated_by = admin so the audit trail records who did the
# migration.
# ============================================================================
"""19.0.6.5 — Backfill x_validated=True on every charity attendance row."""


def migrate(cr, version):
    if not version:
        return
    # Admin user is always id=1 (the __system__ superuser) in Odoo — safe
    # bet for the automated validator stamp.
    cr.execute(
        """
        UPDATE hr_attendance
           SET x_validated = TRUE,
               x_validated_by = COALESCE(x_validated_by, 1),
               x_validated_on = COALESCE(x_validated_on, CURRENT_DATE)
         WHERE x_charity_task_id IS NOT NULL
           AND (x_validated IS FALSE OR x_validated IS NULL)
        """
    )
