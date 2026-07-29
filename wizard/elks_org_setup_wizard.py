# -*- coding: utf-8 -*-
# =============================================================================
# === HUMAN ===
# The "Elks.org Push Setup" wizard a Secretary opens from Configuration
# to see step-by-step Chrome extension install instructions plus the
# on-server path to the extension/ folder so they know where to point
# Chrome's "Load unpacked" (or which folder to zip up and email to a
# Secretary on another computer).  Nothing to install on the server —
# all the auto-push mechanics live inside the extension.
#
# === AI AGENT ===
# TransientModel with a single computed field extension_path_hint,
# resolved via odoo.modules.module.get_module_path('elkscharity').  The
# form view lives in elks_org_setup_wizard_views.xml and does all the
# storytelling — this model exists mostly to give the view a backing
# record.  History: 19.0.2.x versions of this wizard probed and installed
# Playwright + Chromium via subprocess; that code was removed in 19.0.6.2
# after the server-side push path was retired.  See
# migrations/19.0.5.0/pre-migrate.py for the field cleanup.
# =============================================================================
"""Setup helper for the Elks.org Push Chrome extension — see file header."""
import logging
import os

from odoo import fields, models

_logger = logging.getLogger(__name__)


class ElksOrgSetupWizard(models.TransientModel):
    _name = "elks.charity.elks_org_setup_wizard"
    _description = "Elks.org Push Chrome Extension — Install Guide"

    extension_path_hint = fields.Char(
        "Extension Folder Path (on server)",
        compute="_compute_extension_path",
        help="Where the Chrome extension source lives on this Odoo "
             "server.  You need this path when 'Load unpacked' asks "
             "for a folder — but only if the Secretary sits at the "
             "server console.  Normally, ship the extension folder "
             "to the Secretary's laptop (git clone, or zip + email) "
             "and point 'Load unpacked' at THEIR local copy.",
    )

    def _compute_extension_path(self):
        for rec in self:
            try:
                from odoo.modules.module import get_module_path
                mod_path = get_module_path("elkscharity") or ""
                rec.extension_path_hint = (
                    os.path.join(mod_path, "extension")
                    if mod_path else False
                )
            except Exception:
                rec.extension_path_hint = False
