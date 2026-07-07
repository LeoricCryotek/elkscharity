# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# One-time data fix: any task in a charity-parent project without a Grand
# Lodge category gets 9999 "Categories Not Covered" so it rolls up on the
# dashboard instead of falling off the map. Safe to re-run.
# === AI AGENT ===
# Odoo migration: def migrate(cr, version). Post-migrate. Runs when
# upgrading ACROSS 19.0.2.15. Idempotent — only updates rows where
# x_charity_category_id IS NULL. Uses raw cr.execute for speed.
# ============================================================================
"""19.0.2.15 — Backfill missing Charity Category on tasks.

Custom charity tasks created via the standard Odoo project UI often
end up with  x_charity_category_id = NULL  because the field was left
blank.  Without a category they don't roll up on the dashboard, which
means the totals under-report.

Going forward the project_task.create / write hooks default missing
categories to 9999.  This migration applies the same rule to existing
rows so nothing is lost.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Find the id of cat_9999 by xmlid.
    cr.execute("""
        SELECT res_id FROM ir_model_data
        WHERE module = 'elkscharity' AND name = 'cat_9999'
    """)
    row = cr.fetchone()
    if not row:
        _logger.warning(
            "elkscharity 19.0.2.15: cat_9999 xmlid missing — skipping "
            "task backfill.  Add it via GL Categories and re-run the "
            "migration if needed."
        )
        return
    cat_9999_id = row[0]

    # Count how many tasks need backfill (report before/after).
    cr.execute("""
        SELECT COUNT(*)
        FROM project_task pt
        JOIN project_project pp ON pp.id = pt.project_id
        WHERE pp.x_is_charity_parent = TRUE
          AND pt.x_charity_category_id IS NULL
    """)
    n = cr.fetchone()[0]
    if not n:
        _logger.info(
            "elkscharity 19.0.2.15: every charity-parent task already "
            "has a category — nothing to backfill."
        )
        return

    _logger.info(
        "elkscharity 19.0.2.15: backfilling %d uncategorized "
        "charity task(s) to 9999 (Categories Not Covered) so they "
        "roll up on the dashboard.",
        n,
    )

    cr.execute("""
        UPDATE project_task pt
        SET x_charity_category_id = %s
        FROM project_project pp
        WHERE pp.id = pt.project_id
          AND pp.x_is_charity_parent = TRUE
          AND pt.x_charity_category_id IS NULL
    """, (cat_9999_id,))

    _logger.info(
        "elkscharity 19.0.2.15: backfilled %d row(s).  Dashboard "
        "totals will refresh on the next render.",
        cr.rowcount,
    )
