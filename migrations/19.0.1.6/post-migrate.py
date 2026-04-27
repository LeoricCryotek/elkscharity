# -*- coding: utf-8 -*-
"""Post-migration: recompute x_is_charity_line on all timesheet lines.

When elkscharity was first installed, the stored related/computed boolean
x_is_charity_line on account.analytic.line may not have been set correctly
for pre-existing demo/real timesheet entries, leaving NULLs that cause
domain filters to misbehave.  This migration forces the correct value
via direct SQL — much faster than an ORM recompute on large datasets.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    # 1.  Set x_is_charity_line = TRUE only for lines whose task
    #     belongs to a charity-parent project.  Everything else → FALSE.
    _logger.info("Recomputing x_is_charity_line on account_analytic_line …")
    cr.execute("""
        UPDATE account_analytic_line aal
        SET x_is_charity_line = (
            EXISTS (
                SELECT 1
                FROM project_task pt
                JOIN project_project pp ON pp.id = pt.project_id
                WHERE pt.id = aal.task_id
                  AND pp.x_is_charity_parent = TRUE
            )
        )
    """)
    updated = cr.rowcount
    _logger.info("  → %d timesheet lines updated", updated)

    # 2.  Also make sure x_is_charity_parent is FALSE (not NULL) on
    #     every project that isn't a charity parent, so domain filters
    #     using traversal (project_id.x_is_charity_parent = True) work
    #     reliably.
    cr.execute("""
        UPDATE project_project
        SET x_is_charity_parent = FALSE
        WHERE x_is_charity_parent IS NULL
    """)
    _logger.info("  → %d projects had NULL x_is_charity_parent fixed", cr.rowcount)

    # 3.  Same for x_is_charity_activity on project.task
    cr.execute("""
        UPDATE project_task pt
        SET x_is_charity_activity = (
            EXISTS (
                SELECT 1
                FROM project_project pp
                WHERE pp.id = pt.project_id
                  AND pp.x_is_charity_parent = TRUE
            )
        )
    """)
    _logger.info("  → %d tasks had x_is_charity_activity recomputed", cr.rowcount)

    # 4.  Ensure x_validated defaults to FALSE (not NULL) on all lines
    cr.execute("""
        UPDATE account_analytic_line
        SET x_validated = FALSE
        WHERE x_validated IS NULL
    """)
    _logger.info("  → %d timesheet lines had NULL x_validated fixed", cr.rowcount)
