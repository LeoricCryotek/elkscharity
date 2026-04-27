# -*- coding: utf-8 -*-
"""Post-migration: remove stale charity menu items from earlier versions.

Prior versions created separate "Validate Timesheet Hours" and
"Validate Tagged Attendance" menus under Elks Charity → Hours.
These have been replaced by the unified "Hours Awaiting Validation"
menu.  Because the old XML-IDs may no longer exist in the module
manifest, we delete the stale ir.ui.menu records by name.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    stale_names = [
        'Validate Timesheet Hours',
        'Validate Tagged Attendance',
    ]
    for name in stale_names:
        cr.execute(
            "DELETE FROM ir_ui_menu WHERE name = %s",
            (name,),
        )
        if cr.rowcount:
            _logger.info("Removed stale menu item: %s (%d rows)", name, cr.rowcount)
        else:
            _logger.info("Menu item '%s' not found — already clean", name)
