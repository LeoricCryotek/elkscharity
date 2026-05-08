# -*- coding: utf-8 -*-
"""Pre-migration: hand off timecard report XML IDs to elksattendance.

When elkscharity is upgraded to 19.0.1.9 it no longer ships the
timecard report wizard, views, reports, or menus.  Without this
migration Odoo's update process would DELETE those ir.model.data
records (and the underlying database rows) because they vanish from
elkscharity's data files.

Instead we reassign ownership to elksattendance so the records survive.
If elksattendance is installed later (not yet present), we just delete
the old XML IDs cleanly — elksattendance will recreate them on install.
"""
import logging

_logger = logging.getLogger(__name__)

# (old_xmlid_name, new_module, new_xmlid_name)
_XMLID_MOVES = [
    # wizard view
    ('view_elks_timecard_report_wizard_form', 'elksattendance', 'view_elks_timecard_report_wizard_form'),
    # wizard action
    ('action_elks_timecard_report_wizard', 'elksattendance', 'action_elks_timecard_report_wizard'),
    # report actions
    ('action_report_timecard', 'elksattendance', 'action_report_timecard'),
    ('action_report_timecard_pdf', 'elksattendance', 'action_report_timecard_pdf'),
    # qweb template
    ('report_employee_timecard', 'elksattendance', 'report_employee_timecard'),
    # menu items
    ('menu_elkscharity_timecard_report', 'elksattendance', 'menu_elkscharity_timecard_report'),
    ('menu_attendance_timecard_report', 'elksattendance', 'menu_attendance_timecard_report'),
]


def migrate(cr, version):
    if not version:
        return

    # Check whether elksattendance is already installed
    cr.execute(
        "SELECT id FROM ir_module_module "
        "WHERE name = 'elksattendance' AND state IN ('installed', 'to upgrade')"
    )
    elksattendance_installed = bool(cr.fetchone())

    for old_name, new_module, new_name in _XMLID_MOVES:
        if elksattendance_installed:
            # Reassign the XML ID to the new module
            cr.execute("""
                UPDATE ir_model_data
                   SET module = %s, name = %s
                 WHERE module = 'elkscharity' AND name = %s
            """, (new_module, new_name, old_name))
            if cr.rowcount:
                _logger.info(
                    "Reassigned XML ID elkscharity.%s → %s.%s",
                    old_name, new_module, new_name,
                )
        else:
            # elksattendance not installed yet — just delete the XML ID
            # so Odoo doesn't try to clean up a record that will be
            # recreated when elksattendance is installed later.
            cr.execute("""
                DELETE FROM ir_model_data
                 WHERE module = 'elkscharity' AND name = %s
            """, (old_name,))
            if cr.rowcount:
                _logger.info(
                    "Removed XML ID elkscharity.%s (elksattendance not "
                    "yet installed; will be recreated on install)",
                    old_name,
                )

    # Also remove the model access entry for the timecard wizard
    # (elksattendance will provide its own)
    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'elkscharity'
           AND name = 'access_timecard_report_wizard'
    """)
    if cr.rowcount:
        _logger.info("Removed XML ID elkscharity.access_timecard_report_wizard")
