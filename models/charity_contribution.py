# -*- coding: utf-8 -*-
"""Charity Contribution — non-attendance charitable activity entries.

Allows the Secretary or Charity Manager to record contributions that
don't involve individual volunteer attendance: in-kind donations,
venue use, cash gifts, supply donations, etc.  Contributions link to a
charity task (project.task) and roll up into the Grand Lodge report
alongside attendance-based data.

Recurring contributions (e.g. weekly venue donation for a church)
are supported.  A template record generates future draft entries
that must be confirmed by the Secretary, who can adjust the numbers
before confirming.
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError


CONTRIBUTION_TYPE = [
    ('in_kind', 'In-Kind Donation'),
    ('venue', 'Venue / Facility Use'),
    ('cash', 'Cash Donation'),
    ('supplies', 'Supplies / Materials'),
    ('service', 'Professional Service'),
    ('other', 'Other'),
]

FREQUENCY_SELECTION = [
    ('weekly', 'Weekly'),
    ('biweekly', 'Every 2 Weeks'),
    ('monthly', 'Monthly'),
    ('quarterly', 'Quarterly'),
]


class ElksCharityContribution(models.Model):
    """Non-attendance charitable activity entry."""

    _name = "elks.charity.contribution"
    _description = "Charity Contribution"
    _order = "contribution_date desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(
        "Description", required=True, tracking=True,
        help="Brief description of the contribution "
             "(e.g. 'Sunday venue donation to First Baptist Church').",
    )
    contribution_date = fields.Date(
        "Date", required=True,
        default=fields.Date.context_today, index=True, tracking=True,
    )
    contribution_type = fields.Selection(
        CONTRIBUTION_TYPE, string="Type", required=True,
        default='in_kind', tracking=True,
    )

    # ── link to charity task ─────────────────────────────────────
    task_id = fields.Many2one(
        "project.task", string="Charity Activity",
        required=True, index=True, tracking=True,
        domain="[('x_is_charity_activity', '=', True)]",
        help="The charity task this contribution counts toward.",
    )
    project_id = fields.Many2one(
        related="task_id.project_id", store=True, string="Charity Project",
    )
    charity_category_id = fields.Many2one(
        related="task_id.x_charity_category_id", store=True,
        string="GL Category",
    )
    charity_section = fields.Selection(
        related="task_id.x_charity_section", store=True,
        string="GL Section",
    )
    lodge_year = fields.Selection(
        related="task_id.x_lodge_year", store=True, index=True,
    )

    # ── contribution values ──────────────────────────────────────
    cash_value = fields.Monetary(
        "Cash Value", currency_field='currency_id', tracking=True,
        help="Cash, check, or money order donated.  Per the GL Workbook: "
             "WHOLE DOLLARS only — no dollar signs, cents, or decimals.  "
             "For U.S. Savings Bonds, use purchase value, not maturity.",
    )
    non_cash_value = fields.Monetary(
        "Non-Cash Value", currency_field='currency_id', tracking=True,
        help="Fair market value of in-kind goods, venue use, or services.  "
             "Per the GL Workbook: include refreshments, supplies, door "
             "prizes, postage, telephone, donated clothing, eyeglasses, "
             "etc.  WHOLE DOLLARS only.  Use IRS valuation guidelines for "
             "used items.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    # ── people counts (GL Workbook columns B, C, D) ──────────────
    head_count = fields.Integer(
        "(B) People Served",
        help="Total number of people who BENEFITED from this contribution "
             "(Column B on the GL Workbook).  Distinct from # Elks / # "
             "Helpers, which count the volunteers (Columns C / D).",
    )
    elks_count = fields.Integer(
        "(C) # Elks Involved",
        help="Number of Elks members involved (Column C).",
    )
    helper_count = fields.Integer(
        "(D) # Helpers Involved",
        help="Number of non-Elk helpers involved (Column D).",
    )

    # ── hours / miles (GL Workbook columns E, F, G, H) ───────────
    # Added in 19.0.2.11 so a single contribution can hold an entire
    # GL row.  Quick-entry wizard writes here for bulk totals; the
    # dashboard SQL view sums these into the per-category roll-ups.
    elks_hours = fields.Float(
        "(E) Total Elk Hours",
        help="Total Elk hours for this activity (Column E).  Per the GL "
             "Workbook: 6 Elks × 6 hours = 36.  Whole hours only.",
    )
    helper_hours = fields.Float(
        "(F) Total Helper Hours",
        help="Total non-Elk helper hours (Column F).  Whole hours only.",
    )
    elks_miles = fields.Float(
        "(G) Elk Miles",
        help="Total Elk mileage, ROUND TRIP (Column G).  Per the GL "
             "Workbook: people × distance × round trip.  Whole miles.",
    )
    helper_miles = fields.Float(
        "(H) Helper Miles",
        help="Total non-Elk helper mileage, round trip (Column H).",
    )

    # ── recipient ────────────────────────────────────────────────
    recipient_org = fields.Char(
        "Recipient Organization",
        help="External organization receiving this contribution.",
    )

    # ── notes ────────────────────────────────────────────────────
    notes = fields.Text("Notes")

    # ── state ────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirmed', 'Confirmed'),
        ('cancelled', 'Cancelled'),
    ], default='draft', tracking=True, index=True,
       help="Draft: awaiting review.\n"
            "Confirmed: verified and included in reports.\n"
            "Cancelled: excluded from reports.",
    )

    # ── recurrence ───────────────────────────────────────────────
    # These fields belong to the TEMPLATE only.  copy=False ensures
    # they are never carried into a generated draft, even if a future
    # caller forgets to override them in `.copy()`.  An @api.constrains
    # further enforces the invariant: a record with template_id set
    # (= a generated copy) can never itself be a template.
    is_recurring = fields.Boolean(
        "Recurring", tracking=True, copy=False,
        help="Tick this on a TEMPLATE to schedule auto-generated "
             "contributions.  Confirm the template and set the next "
             "generation date; the daily cron will create one draft "
             "contribution per occurrence date.  Generated drafts "
             "themselves are NOT recurring.",
    )
    recurrence_frequency = fields.Selection(
        FREQUENCY_SELECTION, string="Frequency", copy=False,
    )
    recurrence_end_date = fields.Date(
        "Recurrence Ends", copy=False,
        help="Stop generating entries after this date. "
             "Leave blank to continue indefinitely.",
    )
    template_id = fields.Many2one(
        "elks.charity.contribution", string="Generated From",
        readonly=True, ondelete="set null", index=True, copy=False,
        help="The recurring template that created this entry.  Empty "
             "on templates and on manually-entered contributions.",
    )
    generated_ids = fields.One2many(
        "elks.charity.contribution", "template_id",
        string="Generated Entries",
    )
    next_generation_date = fields.Date(
        "Next Generation Date", copy=False,
        help="Date for the next auto-generated entry.  Template-only.",
    )
    event_ids = fields.One2many(
        "calendar.event", "x_charity_contribution_id",
        string="Linked Calendar Events",
        help="Calendar events that drive this contribution's recurrence. "
             "When at least one event is linked, the time-based cron is "
             "skipped — entries are generated when the events fire instead.",
    )
    is_event_driven = fields.Boolean(
        compute="_compute_is_event_driven", store=True,
        string="Event-Driven",
        help="True when the contribution's recurrence is driven by linked "
             "calendar events instead of its own frequency.",
    )

    @api.depends("event_ids")
    def _compute_is_event_driven(self):
        for rec in self:
            rec.is_event_driven = bool(rec.event_ids)

    # ── who ──────────────────────────────────────────────────────
    submitted_by = fields.Many2one(
        "res.users", string="Submitted By",
        default=lambda self: self.env.user, tracking=True,
    )
    confirmed_by = fields.Many2one(
        "res.users", string="Confirmed By",
        readonly=True, tracking=True,
    )
    confirmed_date = fields.Datetime("Confirmed At", readonly=True)

    # ── elks.org submission tracking (added 19.0.2.18) ───────────
    # Each contribution needs to be submitted to elks.org's Local
    # Lodge Reporting page (/grandlodge/charity/local.cfm).  Push is
    # attempted automatically when the Secretary approves; a manual
    # "Mark Submitted" / "Skip" / "Retry" flow exists for cases where
    # auto-push isn't possible (no creds, elks.org site change, etc.).
    x_elks_org_state = fields.Selection(
        [
            ("not_pushed", "Not Submitted"),
            ("pushed", "Submitted"),
            ("skipped", "Skipped (Not Submitting)"),
            ("failed", "Push Failed — Retry"),
        ],
        string="Elks.org Status", default="not_pushed",
        tracking=True, copy=False, index=True,
        help="Where this contribution stands with respect to elks.org "
             "Local Lodge Reporting.  Auto-push attempts run on "
             "Secretary approval and update this field.",
    )
    x_elks_org_pushed_on = fields.Datetime(
        "Submitted to Elks.org At", readonly=True, copy=False,
    )
    x_elks_org_pushed_by = fields.Many2one(
        "res.users", string="Submitted to Elks.org By",
        readonly=True, copy=False,
        help="User whose elks.org credentials were used for the push.",
    )
    x_elks_org_confirmation = fields.Char(
        "Elks.org Confirmation #", readonly=True, copy=False,
        help="Confirmation reference returned by elks.org (when the "
             "site exposes one).  Kept for reconciliation.",
    )
    x_elks_org_last_error = fields.Text(
        "Last Push Error", readonly=True, copy=False,
        help="Truncated error message from the last failed push "
             "attempt.  Cleared on the next successful push.",
    )
    x_elks_org_retry_count = fields.Integer(
        "Push Retry Count", default=0, readonly=True, copy=False,
    )

    # ── actions ──────────────────────────────────────────────────
    def action_confirm(self):
        """Secretary confirms the contribution entry.

        After confirming, if the current user has elks.org credentials
        configured and auto-push enabled, attempts to POST the
        contribution to /grandlodge/charity/local.cfm.  Push failures
        are recorded on the contribution but don't roll back the
        confirmation — the Secretary can retry manually.
        """
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_("Only draft contributions can be confirmed."))
            rec.write({
                'state': 'confirmed',
                'confirmed_by': self.env.user.id,
                'confirmed_date': fields.Datetime.now(),
            })
            rec.message_post(
                body=_(
                    "<strong>Contribution Confirmed</strong><br/>"
                    "%(name)s — $%(cash).2f cash, $%(noncash).2f non-cash.<br/>"
                    "Confirmed by %(who)s.",
                    name=rec.name,
                    cash=rec.cash_value,
                    noncash=rec.non_cash_value,
                    who=self.env.user.name,
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
            # Auto-push to elks.org if the Secretary opted in.
            if (rec.x_elks_org_state == 'not_pushed'
                    and self.env.user.x_elks_org_enabled
                    and self.env.user.x_elks_org_login):
                try:
                    rec._push_to_elks_org()
                except Exception:
                    # Never let a push failure roll back the confirm.
                    # The failure is captured on the record via
                    # x_elks_org_state='failed' + x_elks_org_last_error.
                    pass

    # ── elks.org push actions ────────────────────────────────────
    def action_push_to_elks_org(self):
        """Manually trigger the elks.org push for selected rows."""
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(_(
                    "Only confirmed contributions can be pushed to elks.org."
                ))
            if rec.x_elks_org_state == 'pushed':
                raise UserError(_(
                    "This contribution was already submitted to elks.org "
                    "on %s.", rec.x_elks_org_pushed_on
                ))
            if not self.env.user.x_elks_org_login:
                raise UserError(_(
                    "Set your elks.org login under Preferences → "
                    "Elks.org Credentials before pushing."
                ))
            rec._push_to_elks_org()

    def action_mark_submitted_manually(self):
        """Flag as manually submitted on elks.org (no HTTP push)."""
        for rec in self:
            rec.write({
                'x_elks_org_state': 'pushed',
                'x_elks_org_pushed_on': fields.Datetime.now(),
                'x_elks_org_pushed_by': self.env.user.id,
                'x_elks_org_last_error': False,
            })
            rec.message_post(
                body=_(
                    "<strong>Marked as manually submitted to elks.org</strong>"
                    " by %(who)s.", who=self.env.user.name,
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

    def action_skip_elks_org(self):
        """Mark as skipped — do not submit to elks.org."""
        for rec in self:
            rec.write({
                'x_elks_org_state': 'skipped',
                'x_elks_org_last_error': False,
            })
            rec.message_post(
                body=_(
                    "<strong>Skipped elks.org submission</strong> "
                    "(will not push).",
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )

    def action_reset_elks_org_state(self):
        """Reset back to 'not_pushed' so the auto-push can retry."""
        for rec in self:
            rec.write({
                'x_elks_org_state': 'not_pushed',
                'x_elks_org_last_error': False,
                'x_elks_org_retry_count': 0,
            })

    def action_bulk_push_to_elks_org(self):
        """Bulk push — logs into elks.org ONCE and POSTs every selected
        contribution in the same headless-browser session.  Meant for
        Secretary batch runs (e.g., end-of-month uploading dozens of
        entries at a time)."""
        from ..services.elks_org_client import ElksOrgClient, ElksOrgError

        # Filter to eligible rows only.
        eligible = self.filtered(
            lambda r: r.state == 'confirmed'
                      and r.x_elks_org_state in ('not_pushed', 'failed')
        )
        if not eligible:
            raise UserError(_(
                "No selected contributions are eligible for push.  "
                "Rows must be Confirmed and Not-Yet-Submitted."
            ))

        user = self.env.user
        password = user._elks_org_password_clear()
        if not user.x_elks_org_login or not password:
            raise UserError(_(
                "Set your elks.org credentials under Preferences → "
                "Elks.org Credentials before running a bulk push."
            ))

        # Build the payload list in the same order as `eligible`.
        payloads = [rec._build_elks_org_payload() for rec in eligible]

        client = ElksOrgClient(
            login=user.x_elks_org_login,
            password=password,
            login_url=self.env["ir.config_parameter"].sudo().get_param(
                "elkscharity.elks_org_login_url",
                default="https://www.elks.org/secure/elksLogin.cfm",
            ),
            form_url=self.env["ir.config_parameter"].sudo().get_param(
                "elkscharity.elks_org_form_url",
                default="https://www.elks.org/grandlodge/charity/local.cfm",
            ),
            headless=True,
        )

        try:
            results = client.submit_many(payloads)
        except ElksOrgError as e:
            # Bulk failure BEFORE any per-record submission — usually
            # login or Playwright missing.  Mark none pushed.
            raise UserError(_(
                "Bulk push aborted: %s"
            ) % str(e)[:500])

        pushed, failed = 0, 0
        for rec, (confirmation, err) in zip(eligible, results):
            if err:
                rec.write({
                    'x_elks_org_state': 'failed',
                    'x_elks_org_last_error': err[:1000],
                    'x_elks_org_retry_count':
                        (rec.x_elks_org_retry_count or 0) + 1,
                })
                rec.message_post(
                    body=_(
                        "<strong>Bulk push FAILED for this record</strong>: %(err)s",
                        err=err[:500],
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
                failed += 1
            else:
                rec.write({
                    'x_elks_org_state': 'pushed',
                    'x_elks_org_pushed_on': fields.Datetime.now(),
                    'x_elks_org_pushed_by': user.id,
                    'x_elks_org_confirmation': confirmation or False,
                    'x_elks_org_last_error': False,
                })
                rec.message_post(
                    body=_(
                        "<strong>Bulk-pushed to elks.org</strong>. "
                        "Confirmation: %(ref)s",
                        ref=confirmation or "(none returned)",
                    ),
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
                pushed += 1

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Elks.org Bulk Push"),
                'message': _(
                    "Pushed %(p)d contribution(s), %(f)d failed.  "
                    "Details in each record's chatter.",
                    p=pushed, f=failed,
                ),
                'type': 'success' if failed == 0 else 'warning',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def _log_elks_org_failure(self, error_text, diagnostics):
        """Post a chatter note with the error + attach diagnostics so
        we can see EXACTLY what elks.org returned.

        Two attachment kinds, whichever the client captured:
          * screenshot_png_b64  — Playwright screenshot (legacy)
          * html_snippet        — first 2000 chars of the response body
                                  from the failing request; saved as a
                                  .html file the user can click to open
                                  in a browser tab and inspect.
        """
        import base64
        self.ensure_one()
        attachment_ids = []

        if diagnostics and diagnostics.get("screenshot_png_b64"):
            att = self.env["ir.attachment"].sudo().create({
                "name": "elks_org_failure_%s.png" % (self.id or "new"),
                "type": "binary",
                "datas": diagnostics["screenshot_png_b64"],
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "image/png",
            })
            attachment_ids.append(att.id)

        if diagnostics and diagnostics.get("html_snippet"):
            html = diagnostics["html_snippet"]
            att = self.env["ir.attachment"].sudo().create({
                "name": "elks_org_response_%s.html" % (self.id or "new"),
                "type": "binary",
                "datas": base64.b64encode(html.encode("utf-8")).decode("ascii"),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "text/html",
            })
            attachment_ids.append(att.id)

        parts = [
            "<strong>Elks.org push FAILED</strong>: %s" % (
                (error_text or "")[:600]
            ),
        ]
        if diagnostics:
            if diagnostics.get("url"):
                parts.append(
                    "<br/><em>Landed at:</em> <code>%s</code>"
                    % diagnostics["url"][:300]
                )
            if diagnostics.get("title"):
                parts.append(
                    "<br/><em>Page title:</em> %s"
                    % diagnostics["title"][:200]
                )
            # Inline the first 400 chars of the response body so the
            # Secretary doesn't have to open the attachment for a quick
            # eyeball.
            if diagnostics.get("html_snippet"):
                snippet = diagnostics["html_snippet"][:400]
                # Escape HTML so it doesn't render inside the chatter
                escaped = (
                    snippet.replace("&", "&amp;")
                           .replace("<", "&lt;")
                           .replace(">", "&gt;")
                )
                parts.append(
                    "<br/><br/><em>Response preview:</em>"
                    "<pre style='background:#f5f5f5;padding:8px;"
                    "font-size:11px;max-height:200px;overflow:auto;"
                    "border:1px solid #ddd;'>%s</pre>" % escaped
                )
            if attachment_ids:
                parts.append(
                    "<em>Full response attached above ↑ — open the .html "
                    "file to see exactly what elks.org sent back after "
                    "the login POST.</em>"
                )
        self.message_post(
            body="".join(parts),
            message_type="comment",
            subtype_xmlid="mail.mt_note",
            attachment_ids=attachment_ids,
        )

    def _build_elks_org_payload(self):
        """Return the dict the ElksOrgClient expects for a single push."""
        self.ensure_one()
        cat_code = (self.charity_category_id.code or "").strip()
        program_name = (self.name or self.charity_category_id.name or "")[:50]
        other_program = ""
        if cat_code == "9999":
            other_program = (self.recipient_org or program_name)[:50]
        return {
            "programDate": (self.contribution_date or fields.Date.today())
                              .strftime("%Y-%m-%d"),
            "programID": cat_code,
            "otherProgramID": other_program or "n/a",
            "programName": program_name,
            "headcount": int(self.head_count or 0),
            "numberElks": int(self.elks_count or 0),
            "numberHelpers": int(self.helper_count or 0),
            "hoursElks": int(round(self.elks_hours or 0)),
            "hoursHelpers": int(round(self.helper_hours or 0)),
            "milesElks": int(round(self.elks_miles or 0)),
            "milesHelpers": int(round(self.helper_miles or 0)),
            "nonCash": int(round(self.non_cash_value or 0)),
            "cash": int(round(self.cash_value or 0)),
        }

    def _push_to_elks_org(self):
        """Internal — submit this contribution to elks.org.

        Uses the CURRENT user's stored elks.org credentials.  Updates
        state fields on this record based on the outcome.  Never
        raises out of this method — errors are captured on the record.
        """
        from ..services.elks_org_client import ElksOrgClient, ElksOrgError

        self.ensure_one()
        user = self.env.user
        password = user._elks_org_password_clear()
        if not user.x_elks_org_login or not password:
            self.write({
                'x_elks_org_state': 'failed',
                'x_elks_org_last_error': _(
                    "No elks.org credentials configured for %s.",
                    user.name,
                ),
                'x_elks_org_retry_count': (self.x_elks_org_retry_count or 0) + 1,
            })
            return

        client = ElksOrgClient(
            login=user.x_elks_org_login,
            password=password,
            login_url=self.env["ir.config_parameter"].sudo().get_param(
                "elkscharity.elks_org_login_url",
                default="https://www.elks.org/secure/elksLogin.cfm",
            ),
            form_url=self.env["ir.config_parameter"].sudo().get_param(
                "elkscharity.elks_org_form_url",
                default="https://www.elks.org/grandlodge/charity/local.cfm",
            ),
        )

        payload = self._build_elks_org_payload()

        try:
            confirmation = client.submit_contribution(payload)
            self.write({
                'x_elks_org_state': 'pushed',
                'x_elks_org_pushed_on': fields.Datetime.now(),
                'x_elks_org_pushed_by': user.id,
                'x_elks_org_confirmation': confirmation or False,
                'x_elks_org_last_error': False,
            })
            self.message_post(
                body=_(
                    "<strong>Submitted to elks.org</strong> "
                    "as %(login)s.  Confirmation: %(ref)s",
                    login=user.x_elks_org_login,
                    ref=confirmation or "(none returned)",
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_note',
            )
        except ElksOrgError as e:
            err = str(e)[:1000]
            self.write({
                'x_elks_org_state': 'failed',
                'x_elks_org_last_error': err,
                'x_elks_org_retry_count': (self.x_elks_org_retry_count or 0) + 1,
            })
            self._log_elks_org_failure(err, getattr(e, "diagnostics", {}))

    def action_cancel(self):
        """Cancel the contribution."""
        for rec in self:
            if rec.state == 'confirmed':
                raise UserError(_(
                    "Cannot cancel a confirmed contribution. "
                    "Reset to draft first if you need to cancel."
                ))
            rec.state = 'cancelled'

    def action_reset_draft(self):
        """Reset to draft for re-review."""
        for rec in self:
            rec.write({
                'state': 'draft',
                'confirmed_by': False,
                'confirmed_date': False,
            })

    def action_duplicate_next(self):
        """Quick-duplicate this entry for the next occurrence date."""
        self.ensure_one()
        next_date = self._get_next_date(self.contribution_date)
        new = self.copy({
            'contribution_date': next_date,
            'state': 'draft',
            'template_id': self.id if self.is_recurring else False,
            'confirmed_by': False,
            'confirmed_date': False,
            'is_recurring': False,
            'next_generation_date': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': new.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ── recurrence helpers ───────────────────────────────────────
    def _get_next_date(self, from_date):
        """Calculate the next date based on recurrence frequency."""
        freq = self.recurrence_frequency
        if freq == 'weekly':
            return from_date + timedelta(weeks=1)
        elif freq == 'biweekly':
            return from_date + timedelta(weeks=2)
        elif freq == 'monthly':
            return from_date + relativedelta(months=1)
        elif freq == 'quarterly':
            return from_date + relativedelta(months=3)
        # Default: one week
        return from_date + timedelta(weeks=1)

    @api.model
    def _cron_generate_recurring(self):
        """Cron: generate next draft entries for all active recurring templates.

        Runs daily.  For each recurring contribution whose
        next_generation_date <= today, creates a new draft entry and
        advances the next_generation_date.
        """
        today = fields.Date.context_today(self)
        templates = self.search([
            ('is_recurring', '=', True),
            ('state', '=', 'confirmed'),
            ('next_generation_date', '<=', today),
            '|',
            ('recurrence_end_date', '=', False),
            ('recurrence_end_date', '>=', today),
            # Skip event-driven contributions — those are generated by the
            # event-driven cron when the linked calendar event fires.
            ('is_event_driven', '=', False),
        ])
        for tmpl in templates:
            gen_date = tmpl.next_generation_date
            # Generate entries up to today (in case cron missed days)
            while gen_date and gen_date <= today:
                # Check end date
                if tmpl.recurrence_end_date and gen_date > tmpl.recurrence_end_date:
                    break
                # Don't create if one already exists for this date
                existing = self.search_count([
                    ('template_id', '=', tmpl.id),
                    ('contribution_date', '=', gen_date),
                ])
                if not existing:
                    tmpl.copy({
                        'contribution_date': gen_date,
                        'state': 'draft',
                        'template_id': tmpl.id,
                        'is_recurring': False,
                        'next_generation_date': False,
                        'confirmed_by': False,
                        'confirmed_date': False,
                    })
                gen_date = tmpl._get_next_date(gen_date)

            # Advance the template's next_generation_date
            tmpl.next_generation_date = gen_date

    @api.onchange('is_recurring', 'recurrence_frequency', 'contribution_date')
    def _onchange_recurrence(self):
        """Set next generation date when recurrence is configured."""
        if self.is_recurring and self.recurrence_frequency and self.contribution_date:
            self.next_generation_date = self._get_next_date(
                self.contribution_date
            )
        elif not self.is_recurring:
            self.next_generation_date = False

    # ── validation ───────────────────────────────────────────────
    @api.constrains('cash_value', 'non_cash_value')
    def _check_values(self):
        for rec in self:
            if rec.cash_value < 0 or rec.non_cash_value < 0:
                raise ValidationError(_(
                    "Contribution values cannot be negative."
                ))

    @api.constrains('contribution_date', 'recurrence_end_date')
    def _check_dates(self):
        for rec in self:
            if (rec.recurrence_end_date
                    and rec.contribution_date
                    and rec.recurrence_end_date < rec.contribution_date):
                raise ValidationError(_(
                    "Recurrence end date cannot be before the contribution date."
                ))

    @api.constrains('is_recurring', 'template_id')
    def _check_template_invariant(self):
        """A record cannot be both a template (is_recurring=True) AND a
        generated copy (template_id set).  Generated drafts are leaves
        of the schedule, not roots."""
        for rec in self:
            if rec.is_recurring and rec.template_id:
                raise ValidationError(_(
                    "A generated contribution can't also be a recurring "
                    "template.  Clear the 'Generated From' link, or "
                    "untick 'Recurring' — not both at once."
                ))
