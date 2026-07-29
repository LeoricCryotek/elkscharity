# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# Removes the four legacy elks.org credential columns from res_users:
#   x_elks_org_enabled   (Boolean — turned on the old Playwright auto-push)
#   x_elks_org_login     (Char — member number / email)
#   x_elks_org_password  (Char with password=True — the actual password)
#   x_elks_org_last_success is kept — the extension still uses it.
# The Chrome-extension push path replaces this entirely.  Any stored
# passwords are wiped, and the module's Python + views stop referencing
# these fields.
# === AI AGENT ===
# Runs before the module upgrade replaces the ORM.  Uses raw SQL to
# drop only the three fields being removed; the rest of the res.users
# table + related fields (x_elks_org_api_key, x_elks_org_last_success)
# stay untouched.
# ============================================================================
"""19.0.5.0 — Drop legacy server-side elks.org credentials from res.users."""


def migrate(cr, version):
    # Nothing to do on a fresh install.
    if not version:
        return

    to_drop = [
        "x_elks_org_enabled",
        "x_elks_org_login",
        "x_elks_org_password",
    ]
    for col in to_drop:
        cr.execute(
            """
            SELECT column_name FROM information_schema.columns
             WHERE table_name = 'res_users' AND column_name = %s
            """,
            (col,),
        )
        if cr.fetchone():
            cr.execute(
                f'ALTER TABLE res_users DROP COLUMN IF EXISTS "{col}"'
            )
