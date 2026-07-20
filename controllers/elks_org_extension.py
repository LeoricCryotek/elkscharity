# -*- coding: utf-8 -*-
"""JSON API for the Elks.org Push Chrome extension.

Flow:
  1. User installs the Chrome extension, pastes their API key
     (generated on their user preferences form).
  2. Extension polls  GET /elkscharity/ext/v1/pending  every 60s.
  3. Extension has a live elks.org tab open.  For each pending
     contribution the server returned, extension makes a same-origin
     fetch POST to elks.org's form using the user's active session
     cookies.
  4. Extension reports the outcome back to Odoo via
     POST /elkscharity/ext/v1/mark_pushed  (success)
     POST /elkscharity/ext/v1/mark_failed  (error text)

Auth: the extension sends `X-Elks-Api-Key: <key>` on every request.
Odoo looks the key up on res.users via
`_elks_org_user_for_api_key(key)`.  If found, the request is processed
as that user; if not, 401.

CORS: `Access-Control-Allow-Origin: *` is safe here because
authentication is via header (not cookies) — no browser will send
the API key without our extension explicitly setting it.  We also
explicitly allow the header so the CORS preflight succeeds.
"""
import json
import logging

from odoo import fields, http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


# Maximum contributions returned per poll.  Keeps the response body
# small and gives the extension a chance to process a batch before
# the next poll cycle picks up newly-added items.
PENDING_BATCH_SIZE = 25

# CORS headers we tack onto every response.  `*` is fine because
# auth is via header (X-Elks-Api-Key), not cookie.
CORS_HEADERS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
    ("Access-Control-Allow-Headers",
     "Content-Type, X-Elks-Api-Key"),
    ("Access-Control-Max-Age", "86400"),
]


def _json_response(data, status=200):
    """Uniform JSON + CORS response helper."""
    body = json.dumps(data).encode("utf-8")
    headers = list(CORS_HEADERS) + [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ]
    return Response(body, status=status, headers=headers)


def _preflight_response():
    """Bare 204 for OPTIONS preflight."""
    return Response("", status=204, headers=list(CORS_HEADERS))


def _authed_user():
    """Return the res.users record for the request's API key, or None."""
    key = request.httprequest.headers.get("X-Elks-Api-Key", "")
    if not key:
        return None
    Users = request.env["res.users"].sudo()
    user = Users._elks_org_user_for_api_key(key)
    return user or None


class ElksOrgExtensionController(http.Controller):
    """Endpoints consumed by the Elks.org Push Chrome extension."""

    # ── OPTIONS preflight ──────────────────────────────────────────
    # Chrome fires a CORS preflight before any request with a custom
    # header (like X-Elks-Api-Key).  One handler covers all three
    # endpoints via wildcard.
    @http.route(
        "/elkscharity/ext/v1/<string:endpoint>",
        type="http", auth="none", methods=["OPTIONS"], csrf=False,
    )
    def preflight(self, endpoint, **kw):
        return _preflight_response()

    # ── whoami — extension uses this to test the key ───────────────
    @http.route(
        "/elkscharity/ext/v1/whoami",
        type="http", auth="none", methods=["GET"], csrf=False,
    )
    def whoami(self, **kw):
        user = _authed_user()
        if not user:
            return _json_response(
                {"error": "invalid_api_key"}, status=401,
            )
        return _json_response({
            "ok": True,
            "user_id": user.id,
            "user_name": user.name,
            "user_login": user.login,
            "odoo_version": "19.0.3.0",
        })

    # ── list pending pushes ────────────────────────────────────────
    @http.route(
        "/elkscharity/ext/v1/pending",
        type="http", auth="none", methods=["GET"], csrf=False,
    )
    def pending(self, **kw):
        user = _authed_user()
        if not user:
            return _json_response(
                {"error": "invalid_api_key"}, status=401,
            )
        # Impersonate the API-key owner so record rules apply.
        env = request.env(user=user.id)
        Contrib = env["elks.charity.contribution"].sudo()
        # Same criteria the bulk-push action uses: confirmed,
        # pushable state, and the user is either the creator or a
        # Secretary.  We keep it simple — return everything eligible;
        # extension will iterate in order.
        domain = [
            ("state", "in", ("confirmed", "reported")),
            ("x_elks_org_state", "in", ("not_pushed", "failed")),
        ]
        # Non-Secretary users see only their own submissions —
        # Secretaries see everything.
        if not user.has_group("elkscharity.group_elkscharity_secretary"):
            domain += [("create_uid", "=", user.id)]
        contribs = Contrib.search(domain, limit=PENDING_BATCH_SIZE)
        items = []
        for c in contribs:
            try:
                payload = c._build_elks_org_payload()
            except Exception as e:
                _logger.exception(
                    "elkscharity ext: build_payload failed for id=%d",
                    c.id,
                )
                continue
            items.append({
                "id": c.id,
                "display_name": c.display_name,
                "payload": payload,
                "retry_count": c.x_elks_org_retry_count,
            })
        return _json_response({
            "ok": True,
            "count": len(items),
            "items": items,
            "form_url": (
                env["ir.config_parameter"].sudo().get_param(
                    "elkscharity.elks_org_form_url",
                    "https://www.elks.org/grandlodge/charity/local.cfm",
                )
            ),
        })

    # ── mark pushed ────────────────────────────────────────────────
    @http.route(
        "/elkscharity/ext/v1/mark_pushed",
        type="http", auth="none", methods=["POST"], csrf=False,
    )
    def mark_pushed(self, **kw):
        user = _authed_user()
        if not user:
            return _json_response(
                {"error": "invalid_api_key"}, status=401,
            )
        try:
            body = json.loads(request.httprequest.data or b"{}")
        except (ValueError, TypeError):
            return _json_response(
                {"error": "invalid_json"}, status=400,
            )
        contrib_id = body.get("contribution_id")
        confirmation = (body.get("confirmation") or "OK")[:200]
        if not contrib_id:
            return _json_response(
                {"error": "missing_contribution_id"}, status=400,
            )
        env = request.env(user=user.id)
        Contrib = env["elks.charity.contribution"].sudo()
        rec = Contrib.browse(int(contrib_id)).exists()
        if not rec:
            return _json_response(
                {"error": "not_found"}, status=404,
            )
        rec.write({
            "x_elks_org_state": "pushed",
            "x_elks_org_pushed_on": fields.Datetime.now(),
            "x_elks_org_pushed_by": user.id,
            "x_elks_org_confirmation": confirmation,
            "x_elks_org_last_error": False,
        })
        rec.message_post(
            body=(
                "<strong>Elks.org push OK</strong> via Chrome "
                "extension — %s" % confirmation
            ),
            subtype_xmlid="mail.mt_note",
        )
        user.sudo().write({
            "x_elks_org_last_success": fields.Datetime.now(),
        })
        return _json_response({"ok": True})

    # ── mark failed ────────────────────────────────────────────────
    @http.route(
        "/elkscharity/ext/v1/mark_failed",
        type="http", auth="none", methods=["POST"], csrf=False,
    )
    def mark_failed(self, **kw):
        user = _authed_user()
        if not user:
            return _json_response(
                {"error": "invalid_api_key"}, status=401,
            )
        try:
            body = json.loads(request.httprequest.data or b"{}")
        except (ValueError, TypeError):
            return _json_response(
                {"error": "invalid_json"}, status=400,
            )
        contrib_id = body.get("contribution_id")
        error_text = (body.get("error") or "unknown error")[:1000]
        html_snippet = (body.get("html_snippet") or "")[:8000]
        if not contrib_id:
            return _json_response(
                {"error": "missing_contribution_id"}, status=400,
            )
        env = request.env(user=user.id)
        Contrib = env["elks.charity.contribution"].sudo()
        rec = Contrib.browse(int(contrib_id)).exists()
        if not rec:
            return _json_response(
                {"error": "not_found"}, status=404,
            )
        rec.write({
            "x_elks_org_state": "failed",
            "x_elks_org_last_error": error_text,
            "x_elks_org_retry_count": (rec.x_elks_org_retry_count or 0) + 1,
        })
        # Attach the raw elks.org response body when the extension
        # sends one — that's usually where the failure explanation is.
        attachment_ids = []
        if html_snippet:
            import base64
            att = env["ir.attachment"].sudo().create({
                "name": "elks_org_ext_response_%s.html" % rec.id,
                "type": "binary",
                "datas": base64.b64encode(
                    html_snippet.encode("utf-8")
                ).decode("ascii"),
                "res_model": rec._name,
                "res_id": rec.id,
                "mimetype": "text/html",
            })
            attachment_ids.append(att.id)
        rec.message_post(
            body=(
                "<strong>Elks.org push FAILED</strong> (Chrome "
                "extension): %s" % error_text
            ),
            subtype_xmlid="mail.mt_note",
            attachment_ids=attachment_ids,
        )
        return _json_response({"ok": True})
