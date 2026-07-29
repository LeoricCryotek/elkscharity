# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# One-time cleanup that runs when the module upgrades to 19.0.6.2.
# Drops the obsolete elks.org login-URL config parameter — nobody
# references it since the Playwright/server-side push was removed.
# === AI AGENT ===
# post-migrate.py runs AFTER the ORM is loaded.  Uses cr.execute for
# a direct delete on ir_config_parameter — safe and idempotent
# (WHERE clause matches by key).  Nothing else has a foreign key on
# config parameters so the delete is standalone.
# ============================================================================
"""19.0.6.2 — Delete the obsolete elks_org_login_url config parameter."""


def migrate(cr, version):
    if not version:
        return
    cr.execute(
        """
        DELETE FROM ir_config_parameter
         WHERE key = 'elkscharity.elks_org_login_url'
        """
    )
