# -*- coding: utf-8 -*-
"""19.0.2.4 — Retire category 1702 (ENF Donation by Lodge/Assn).

The Grand Lodge workbook only defines code 1701 in the ENF section.
We previously seeded an extra 1702 ("ENF Donation by Lodge/Assn") as
a lodge-internal convenience, but this drifts from the official list.

This migration:
    1. Reassigns every project.task whose x_charity_category_id points
       at the old 1702 record to 1701 (ENF Donations) — they're in the
       same GL section so the Grand Lodge report total is unchanged.
    2. Reassigns every elks.charity.contribution the same way (via the
       task link — contributions reach the category through the task).
    3. Deletes the 1702 category record and its ir_model_data entry so
       the data file stays in sync with the database.
    4. Logs counts so the Secretary can verify in the upgrade log.

Safe to re-run: each step checks for existence before acting.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    # Look up the two category record ids by xmlid.
    cr.execute("""
        SELECT name, res_id
        FROM ir_model_data
        WHERE module = 'elkscharity'
          AND name IN ('cat_1701', 'cat_1702')
    """)
    by_name = dict(cr.fetchall())
    old_id = by_name.get('cat_1702')
    new_id = by_name.get('cat_1701')

    if not old_id:
        _logger.info(
            "elkscharity 19.0.2.4: cat_1702 not present, nothing to migrate."
        )
        return

    if not new_id:
        _logger.warning(
            "elkscharity 19.0.2.4: cat_1701 (ENF Donations) is missing — "
            "cannot reassign tasks from cat_1702.  Aborting."
        )
        return

    # 1. Reassign tasks
    cr.execute("""
        UPDATE project_task
        SET x_charity_category_id = %s
        WHERE x_charity_category_id = %s
    """, (new_id, old_id))
    n_tasks = cr.rowcount
    _logger.info(
        "elkscharity 19.0.2.4: reassigned %d project.task row(s) from "
        "1702 → 1701.", n_tasks,
    )

    # 2. Contributions reach the category through task_id; the task
    #    reassignment above covers them transitively (since the related
    #    field updates on next read).  But if any contribution stored a
    #    category_id directly via the stored related, force a recompute.
    #    No direct stored category column on contribution; nothing more
    #    to do here.

    # 3. Delete the 1702 category row and its ir_model_data entry.
    cr.execute("DELETE FROM elks_charity_category WHERE id = %s", (old_id,))
    cr.execute("""
        DELETE FROM ir_model_data
        WHERE module = 'elkscharity' AND name = 'cat_1702'
    """)
    _logger.info(
        "elkscharity 19.0.2.4: removed cat_1702 record and ir_model_data "
        "entry.  Module now strictly matches the 30-category Grand Lodge "
        "workbook."
    )
