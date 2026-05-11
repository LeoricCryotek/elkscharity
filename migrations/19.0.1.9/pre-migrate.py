# -*- coding: utf-8 -*-
"""Pre-migration: hand off timecard report XML IDs to elksattendance.

When elkscharity is upgraded to 19.0.1.9 it no longer ships the
timecard report wizard, views, reports, or menus.  Without this
migration Odoo's update process would DELETE those ir.model.data
records (and the underlying database rows) because they vanish from
elkscharity's data files.

Instead we reassign ownership to elksattendance so the records survive.
If elksattendance already owns the XML ID, we just delete the
elkscharity copy.  If elksattendance is not installed yet, we delete
the old XML IDs cleanly — elksattendance will recreate them on install.
"""
import logging

_logger = logging.getLogger(__name__)

# (old_xmlid_name, new_module, new_xmlid_name)
_XMLID_MOVES = [
    ('view_elks_timecard_report_wizard_form', 'elksattendance', 'view_elks_timecard_report_wizard_form'),
    ('action_elks_timecard_report_wizard', 'elksattendance', 'action_elks_timecard_report_wizard'),
    ('action_report_timecard', 'elksattendance', 'action_report_timecard'),
    ('action_report_timecard_pdf', 'elksattendance', 'action_report_timecard_pdf'),
    ('report_employee_timecard', 'elksattendance', 'report_employee_timecard'),
    ('menu_elkscharity_timecard_report', 'elksattendance', 'menu_elkscharity_timecard_report'),
    ('menu_attendance_timecard_report', 'elksattendance', 'menu_attendance_timecard_report'),
]


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        "SELECT id FROM ir_module_module "
        "WHERE name = 'elksattendance' AND state IN ('installed', 'to upgrade')"
    )
    elksattendance_installed = bool(cr.fetchone())

    for old_name, new_module, new_name in _XMLID_MOVES:
        if elksattendance_installed:
            # Check if the target already owns this XML ID
            cr.execute(
                "SELECT id FROM ir_model_data WHERE module = %s AND name = %s",
                (new_module, new_name),
            )
            target_exists = bool(cr.fetchone())

            if target_exists:
                # Target already owns it — just delete the elkscharity copy
                cr.execute(
                    "DELETE FROM ir_model_data WHERE module = 'elkscharity' AND name = %s",
                    (old_name,),
                )
                if cr.rowcount:
                    _logger.info(
                        "Removed duplicate XML ID elkscharity.%s "
                        "(%s.%s already exists)",
                        old_name, new_module, new_name,
                    )
            else:
                # Reassign the XML ID to the new module
                cr.execute("""
                    UPDATE ir_model_data
                       SET module = %s, name = %s
                     WHERE module = 'elkscharity' AND name = %s
                """, (new_module, new_name, old_name))
                if cr.rowcount:
                    _logger.info(
                        "Reassigned XML ID elkscharity.%s -> %s.%s",
                        old_name, new_module, new_name,
                    )
        else:
            cr.execute(
                "DELETE FROM ir_model_data WHERE module = 'elkscharity' AND name = %s",
                (old_name,),
            )
            if cr.rowcount:
                _logger.info(
                    "Removed XML ID elkscharity.%s (elksattendance not "
                    "yet installed; will be recreated on install)",
                    old_name,
                )

    cr.execute("""
        DELETE FROM ir_model_data
         WHERE module = 'elkscharity'
           AND name = 'access_timecard_report_wizard'
    """)
    if cr.rowcount:
        _logger.info("Removed XML ID elkscharity.access_timecard_report_wizard")
