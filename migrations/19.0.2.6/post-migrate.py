# -*- coding: utf-8 -*-
"""19.0.2.6 — Clean up Recurring Templates list.

Before 19.0.2.6 the cron's ``copy()`` relied on the caller passing
``is_recurring=False`` in the default dict.  In rare cases (e.g. the
user re-flagged a generated draft as recurring through the form, or
an older cron version didn't pass the override) a generated copy
ended up with both ``is_recurring=True`` AND ``template_id`` set,
which made it show up under "Recurring Templates" alongside the
actual master template.

From 19.0.2.6 onwards the constraint ``_check_template_invariant``
prevents this combination, and the recurrence fields are marked
``copy=False`` so generated copies can never inherit them.

This migration scrubs the existing data so the new constraint can
load without firing on legacy rows.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("""
        SELECT COUNT(*)
        FROM elks_charity_contribution
        WHERE template_id IS NOT NULL
          AND is_recurring = TRUE
    """)
    n = cr.fetchone()[0]
    if not n:
        _logger.info(
            "elkscharity 19.0.2.6: no generated drafts mis-flagged as "
            "recurring templates — nothing to clean up."
        )
        return

    _logger.info(
        "elkscharity 19.0.2.6: %d generated contribution(s) were "
        "incorrectly flagged as recurring templates.  Clearing the "
        "template-only fields on them so they appear under regular "
        "Contributions / Pending Review instead of Recurring Templates.",
        n,
    )
    cr.execute("""
        UPDATE elks_charity_contribution
        SET is_recurring = FALSE,
            recurrence_frequency = NULL,
            recurrence_end_date = NULL,
            next_generation_date = NULL
        WHERE template_id IS NOT NULL
          AND is_recurring = TRUE
    """)
    _logger.info(
        "elkscharity 19.0.2.6: cleaned %d row(s); Recurring Templates "
        "list will now show only true templates.",
        cr.rowcount,
    )
