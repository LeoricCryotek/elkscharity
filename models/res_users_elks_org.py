# -*- coding: utf-8 -*-
"""Per-user API key for the Elks.org Push Chrome extension.

The extension uses the user's live browser session for elks.org auth
(cookies from the actual www.elks.org tab), so we never touch or store
elks.org credentials on the Odoo side — only the API key that the
extension uses to talk BACK to Odoo.

History note: earlier versions (19.0.2.x) tried server-side push via
Playwright + stored login/password.  Elks.org's bot detection blocked
that path definitively.  As of 19.0.5.0 the legacy fields (login,
password, enabled, last_success) are removed — see the pre-migrate in
migrations/19.0.5.0/pre-migrate.py for the DB cleanup.
"""
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResUsersElksOrg(models.Model):
    _inherit = "res.users"

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
    x_elks_org_last_success = fields.Datetime(
        "Last Successful Push", readonly=True,
        help="Timestamp of the most recent successful contribution "
             "submission attributed to this user (set by the extension "
             "controller via mark_pushed).",
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "x_elks_org_last_success",
            "x_elks_org_api_key",
            "x_elks_org_api_key_created",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        # api_key is intentionally NOT self-writeable — the user can
        # only change it via action_regenerate_elks_org_api_key so we
        # control the format and always update the created-on timestamp.
        return super().SELF_WRITEABLE_FIELDS

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

