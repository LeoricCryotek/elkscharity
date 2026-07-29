# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# Cleanup for 19.0.6.10.  Deletes the retired "Hours Awaiting
# Validation" menu record left over from earlier installs where the
# menuitem was declared in XML.  Since 19.0.6.5, charity attendance
# auto-validates on save so the list would always be empty; the menu
# is now removed entirely.
# === AI AGENT ===
# post-migrate uses cr.execute for a direct row delete against
# ir_ui_menu, joined through ir_model_data on the xmlid.  Idempotent
# — nothing to do on fresh installs (the ir_model_data row won't
# exist).
# ============================================================================
"""19.0.6.10 — Remove the retired 'Hours Awaiting Validation' menu."""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        DELETE FROM ir_ui_menu
         WHERE id IN (
             SELECT res_id FROM ir_model_data
              WHERE module = 'elkscharity'
                AND name   = 'menu_elkscharity_hours_validate'
                AND model  = 'ir.ui.menu'
         )
        """
    )
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'elkscharity'
           AND name   = 'menu_elkscharity_hours_validate'
        """
    )
