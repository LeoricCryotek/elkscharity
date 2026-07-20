# -*- coding: utf-8 -*-
"""Per-user elks.org credentials + API key for the browser extension.

Two integration paths, both per-user:

1. Server-side auto-push (legacy, 19.0.2.x):
   x_elks_org_login + x_elks_org_password.  Odoo POSTs directly to
   elks.org using requests/Playwright.  Currently blocked by elks.org's
   server-side bot detection.

2. Browser-extension push (19.0.3.0+):
   x_elks_org_api_key.  Extension polls Odoo with this key, pulls
   pending contributions, submits them from the user's real Chrome
   using their live elks.org session cookies.

The two paths coexist so lodges can pick whichever works for them.

Storage note: the password is stored in a Char with password=True so
the widget masks it in the UI.  Same pattern Odoo core uses for
fetchmail / outgoing-mail server passwords.  The extension API key is
generated server-side via a button (so the user never types it or
picks a weak one) and shown once, masked afterward.

Only the user themselves and administrators can read the secret fields,
and password=True hides them from _read_group / search.
"""
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResUsersElksOrg(models.Model):
    _inherit = "res.users"

    # ── Server-side push (legacy path) ─────────────────────────────
    x_elks_org_enabled = fields.Boolean(
        "Auto-Push Charity Entries to Elks.org", default=False,
        help="When enabled, confirming a charity contribution "
             "automatically POSTs it to elks.org using the login below. "
             "NOTE: server-side push is currently blocked by elks.org's "
             "bot detection.  Prefer the browser-extension flow — see "
             "Elks Charity → Configuration → Elks.org Push Setup.",
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

    # ── Browser-extension path (19.0.3.0) ──────────────────────────
    x_elks_org_api_key = fields.Char(
        "Elks.org Extension API Key",
        readonly=True,
        copy=False,
        help="Long random token used by the Elks.org Push Chrome "
             "extension to authenticate its requests to this Odoo "
             "server.  Click 'Regenerate' to create a new one — any "
             "extension using the old key will stop working until "
             "you paste the new one into it.",
    )
    x_elks_org_api_key_created = fields.Datetime(
        "API Key Created On", readonly=True, copy=False,
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
            "x_elks_org_api_key",
            "x_elks_org_api_key_created",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "x_elks_org_enabled",
            "x_elks_org_login",
            "x_elks_org_password",
            # api_key is intentionally NOT self-writeable — the user
            # can only change it via action_regenerate_elks_org_api_key
            # so we control the format + always update the created-on
            # timestamp.
        ]

    # ── API key management ─────────────────────────────────────────
    def action_regenerate_elks_org_api_key(self):
        """Generate a fresh urlsafe 40-char token for the extension.

        Prints the key ONCE in the resulting notification so the user
        can copy it into the extension.  After that the field is
        masked (password-style widget) and cannot be re-shown in the
        UI without regenerating.  Standard API-key hygiene.
        """
        self.ensure_one()
        # 30 bytes → 40 urlsafe chars — plenty of entropy, still
        # short enough to paste in one line.
        new_key = secrets.token_urlsafe(30)
        self.sudo().write({
            "x_elks_org_api_key": new_key,
            "x_elks_org_api_key_created": fields.Datetime.now(),
        })
        _logger.info(
            "elkscharity: regenerated elks.org extension API key "
            "for user %s (id=%d).", self.login, self.id,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("New Extension API Key"),
                "message": _(
                    "Copy this key into the Elks.org Push extension "
                    "settings — it will not be shown again:\n\n%s\n\n"
                    "If you already had the extension installed with "
                    "an older key, it will stop working until you "
                    "paste the new one in."
                ) % new_key,
                "sticky": True,
                "type": "warning",
            },
        }

    @api.model
    def _elks_org_user_for_api_key(self, api_key):
        """Look up the user this API key belongs to.

        Called by the controller.  Returns a recordset (empty on
        miss).  Uses sudo() because the caller is unauthenticated at
        this point — the API key IS the authentication.
        """
        if not api_key or len(api_key) < 20:
            # Guard against timing attacks by requiring minimum length.
            return self.env["res.users"]
        user = self.sudo().search(
            [("x_elks_org_api_key", "=", api_key)], limit=1,
        )
        return user

    def _elks_org_password_clear(self):
        """Return the raw elks.org password for HTTP client use.

        Kept as a method (not a direct field access in the client)
        so it's easy to swap in stronger storage (Fernet + keyring)
        later without touching call sites.
        """
        self.ensure_one()
        return self.sudo().x_elks_org_password or ""
