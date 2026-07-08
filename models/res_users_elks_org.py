# -*- coding: utf-8 -*-
"""Per-user elks.org credentials for the Local Lodge Reporting auto-push.

Each Secretary (or any user who submits contributions) can store their
own elks.org member login here.  When they confirm a contribution, the
charity_contribution.action_confirm hook uses THEIR credentials to POST
the entry to /grandlodge/charity/local.cfm.

Storage note: the password is stored in a Char with password=True so
the widget masks it in the UI.  This is the same pattern Odoo core uses
for fetchmail / outgoing-mail server passwords.  At-rest encryption
would require additional key management the lodge probably doesn't
want to run; if you need FIPS-grade storage, keep the auto-push off
and use "Mark as Manually Submitted" instead.

Only the user themselves and administrators can read the field, and
_read_group / search never return it (password=True hides it).
"""
from odoo import _, api, fields, models


class ResUsersElksOrg(models.Model):
    _inherit = "res.users"

    x_elks_org_enabled = fields.Boolean(
        "Auto-Push Charity Entries to Elks.org", default=False,
        help="When enabled, confirming a charity contribution "
             "automatically POSTs it to elks.org using the login below.",
    )
    x_elks_org_login = fields.Char(
        "Elks.org Login",
        help="Your elks.org member login (typically your member number "
             "or email — same value you use at https://www.elks.org/login.cfm).",
    )
    x_elks_org_password = fields.Char(
        "Elks.org Password",
        help="Your elks.org password.  Masked in the UI.  Reset via "
             "elks.org, then update it here.",
    )
    x_elks_org_last_success = fields.Datetime(
        "Last Successful Push", readonly=True,
    )

    # Include the new fields in the user preferences form so users
    # can self-service without needing admin access.
    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "x_elks_org_enabled",
            "x_elks_org_login",
            "x_elks_org_password",
            "x_elks_org_last_success",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "x_elks_org_enabled",
            "x_elks_org_login",
            "x_elks_org_password",
        ]

    def _elks_org_password_clear(self):
        """Return the raw elks.org password for HTTP client use.

        Kept as a method (not a direct field access in the client)
        so it's easy to swap in stronger storage (Fernet + keyring)
        later without touching call sites.
        """
        self.ensure_one()
        return self.sudo().x_elks_org_password or ""
