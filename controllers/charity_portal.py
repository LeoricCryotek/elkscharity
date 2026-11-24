# -*- coding: utf-8 -*-
"""Member portal — Data Collection Survey form.

Logged-in lodge members (or any portal user) can submit charity
activity entries through /my/charity without needing backend access.
Mirrors the BPOE paper form column-for-column (A through J).

Routes:
    GET  /my                        — portal home; adds "Charity Entries" tile
    GET  /my/charity                — list of this user's submissions
    GET  /my/charity/new            — form
    POST /my/charity/new            — submit (creates contribution + optional
                                       personal-record line if the user's
                                       partner is linked to an employee)

All ORM access runs sudo() since portal users have no direct ACL on
charity models.  We attribute the contribution to the logged-in user
via the existing  submitted_by  field so submissions are auditable.
"""
import logging

from odoo import _, fields, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.osv import expression
from odoo.addons.portal.controllers.portal import CustomerPortal

_logger = logging.getLogger(__name__)


def _lodge_year_for_date(d):
    """Return 'YYYY-YYYY' lodge year for a given date (Apr 1 → Mar 31)."""
    if not d:
        return None
    if d.month >= 4:
        return f"{d.year}-{d.year + 1}"
    return f"{d.year - 1}-{d.year}"


def _safe_float(v):
    try:
        return float(v) if v not in (None, "", False) else 0.0
    except (ValueError, TypeError):
        return 0.0


def _safe_int(v):
    try:
        return int(v) if v not in (None, "", False) else 0
    except (ValueError, TypeError):
        return 0


class ElksCharityPortal(CustomerPortal):

    # ─────────────────────────────────────────────────────────────
    # Portal home tile
    # ─────────────────────────────────────────────────────────────
    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "charity_count" in counters:
            partner = request.env.user.partner_id
            values["charity_count"] = (
                request.env["elks.charity.contribution"]
                .sudo()
                .search_count([("submitted_by", "=", request.env.user.id)])
            )
        return values

    # ─────────────────────────────────────────────────────────────
    # Who the hours count towards
    # ─────────────────────────────────────────────────────────────
    def _credit_candidate_domain(self):
        """Who may be named as the member getting the credit.

        Deliberately NOT restricted to internal users. A lodge member is a
        PORTAL user, and they are exactly who the Secretary needs to credit
        when hours come in by phone — filtering to share=False would leave
        only staff and officers on the list, which is the opposite of useful.

        Excluded: the public (not-logged-in) user and OdooBot, neither of
        which is a person who can volunteer.
        """
        excluded = []
        for xmlid in ("base.public_user", "base.user_root"):
            rec = request.env.ref(xmlid, raise_if_not_found=False)
            if rec:
                excluded.append(rec.id)
        domain = [("active", "=", True)]
        if excluded:
            domain.append(("id", "not in", excluded))
        return domain

    def _credit_user_values(self):
        """Default the credited member to whoever is logged in.

        A portal member filing their own hours is the normal case, so the
        field defaults to them and is shown read-only — letting any portal
        user post hours onto another member's record would be an integrity
        hole, since these figures reach the Grand Lodge report and a member's
        personal history.

        Internal users (the Secretary taking hours over the phone) may pick
        somebody else. The field is SHOWN either way: previously nobody was
        told whose record the hours landed on.
        """
        user = request.env.user
        can_reassign = user.has_group("base.group_user")
        candidates = request.env["res.users"].sudo().browse()
        if can_reassign:
            candidates = request.env["res.users"].sudo().search(
                self._credit_candidate_domain(), order="name") | user
        return {
            "credit_user": user,
            "can_reassign": can_reassign,
            "credit_candidates": candidates,
        }

    def _resolve_credit_user(self, post):
        """The member these hours are credited to, for this submission.

        Falls back to the logged-in user whenever the posted value is absent,
        unparseable, or not allowed — a tampered form field must never move
        somebody else's credit.
        """
        user = request.env.user
        if not user.has_group("base.group_user"):
            return user
        wanted = _safe_int(post.get("credit_user_id"))
        if not wanted or wanted == user.id:
            return user
        other = request.env["res.users"].sudo().search(
            expression.AND([self._credit_candidate_domain(),
                            [("id", "=", wanted)]]), limit=1)
        return other or user

    # ─────────────────────────────────────────────────────────────
    # List of this user's submissions
    # ─────────────────────────────────────────────────────────────
    @http.route(
        ["/my/charity"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_my_charity_list(self, **kw):
        contribs = (
            request.env["elks.charity.contribution"]
            .sudo()
            .search(
                [("submitted_by", "=", request.env.user.id)],
                order="contribution_date desc, id desc",
                limit=200,
            )
        )
        values = self._prepare_portal_layout_values()
        values.update({
            "contributions": contribs,
            "page_name": "charity",
        })
        return request.render(
            "elkscharity.portal_my_charity_list", values
        )

    # ─────────────────────────────────────────────────────────────
    # New-entry form (GET + POST)
    # ─────────────────────────────────────────────────────────────
    @http.route(
        ["/my/charity/new"],
        type="http",
        auth="user",
        website=True,
        methods=["GET", "POST"],
    )
    def portal_my_charity_new(self, **post):
        if request.httprequest.method == "POST":
            return self._submit_charity_form(post)

        categories = (
            request.env["elks.charity.category"]
            .sudo()
            .search([("active", "=", True)], order="code")
        )
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "charity",
            "categories": categories,
            "today": fields.Date.context_today(request.env.user),
            "errors": {},
            "form": {},
        })
        values.update(self._credit_user_values())
        return request.render(
            "elkscharity.portal_my_charity_form", values
        )

    # ─────────────────────────────────────────────────────────────
    # Form submission handler
    # ─────────────────────────────────────────────────────────────
    def _submit_charity_form(self, post):
        # Validate
        errors = {}
        event_date_str = (post.get("event_date") or "").strip()
        try:
            event_date = fields.Date.from_string(event_date_str)
        except (ValueError, TypeError):
            event_date = None
        if not event_date:
            errors["event_date"] = "Please enter a valid date."

        category_id = _safe_int(post.get("charity_category_id"))
        if not category_id:
            errors["charity_category_id"] = "Please choose a GL category."
        category = (
            request.env["elks.charity.category"]
            .sudo()
            .browse(category_id)
            .exists()
        )
        if category_id and not category:
            errors["charity_category_id"] = "Category not found."

        program_name = (post.get("program_name") or "").strip()
        if not program_name:
            errors["program_name"] = "Please describe the program."

        # Re-render the form with errors if validation failed
        if errors:
            categories = (
                request.env["elks.charity.category"]
                .sudo()
                .search([("active", "=", True)], order="code")
            )
            values = self._prepare_portal_layout_values()
            values.update({
                "page_name": "charity",
                "categories": categories,
                "today": fields.Date.context_today(request.env.user),
                "errors": errors,
                "form": post,
            })
            values.update(self._credit_user_values())
            return request.render(
                "elkscharity.portal_my_charity_form", values
            )

        # Find / create the charity-parent project for the lodge year
        Project = request.env["project.project"].sudo()
        lodge_year = _lodge_year_for_date(event_date)
        project = Project.create_charity_parent_project(
            lodge_year=lodge_year
        )
        if project.x_is_closed:
            errors["event_date"] = (
                "Lodge year %s is closed — submissions are locked."
                % lodge_year
            )
            categories = (
                request.env["elks.charity.category"]
                .sudo()
                .search([("active", "=", True)], order="code")
            )
            values = self._prepare_portal_layout_values()
            values.update({
                "page_name": "charity",
                "categories": categories,
                "today": fields.Date.context_today(request.env.user),
                "errors": errors,
                "form": post,
            })
            values.update(self._credit_user_values())
            return request.render(
                "elkscharity.portal_my_charity_form", values
            )

        # Find / create the task
        Task = request.env["project.task"].sudo()
        task = Task.search([
            ("project_id", "=", project.id),
            ("x_charity_category_id", "=", category.id),
        ], limit=1)
        head_count = _safe_int(post.get("head_count"))
        if not task:
            task = Task.create({
                "name": program_name,
                "project_id": project.id,
                "x_charity_category_id": category.id,
                "x_event_date": event_date,
                "x_head_count": head_count,
            })

        # Create the bulk contribution
        committee = (post.get("committee") or "").strip()
        contribution_name = (
            f"{program_name} — {committee}" if committee else program_name
        )
        contribution = request.env["elks.charity.contribution"].sudo().create({
            "name": contribution_name,
            "contribution_date": event_date,
            "contribution_type": "other",
            "task_id": task.id,
            "head_count": head_count,
            "elks_count": _safe_int(post.get("elks_count")),
            "helper_count": _safe_int(post.get("helper_count")),
            "elks_hours": _safe_float(post.get("elks_hours")),
            "helper_hours": _safe_float(post.get("helper_hours")),
            "elks_miles": _safe_float(post.get("elks_miles")),
            "helper_miles": _safe_float(post.get("helper_miles")),
            "cash_value": _safe_float(post.get("cash_value")),
            "non_cash_value": _safe_float(post.get("non_cash_value")),
            "currency_id": request.env.company.currency_id.id,
            "recipient_org": committee or False,
            "state": "draft",  # Secretary still reviews before GL-confirm
            "submitted_by": request.env.user.id,
        })

        # Personal credit goes to the member named on the form, which defaults
        # to whoever is logged in. Previously it always followed the submitter
        # with no way to see or change that, so a Secretary taking hours over
        # the phone credited herself.
        credit_user = self._resolve_credit_user(post)
        partner = credit_user.partner_id
        employee_id = partner.x_volunteer_employee_id.id if hasattr(
            partner, "x_volunteer_employee_id"
        ) and partner.x_volunteer_employee_id else False
        elks_hours = _safe_float(post.get("elks_hours"))
        if employee_id and elks_hours:
            request.env["account.analytic.line"].sudo().create({
                "name": f"[Portal Entry] {program_name}",
                "date": event_date,
                "employee_id": employee_id,
                "project_id": project.id,
                "task_id": task.id,
                "unit_amount": elks_hours,
                "x_miles": _safe_float(post.get("elks_miles")),
                "x_is_helper": False,
                "x_personal_record": True,
                "x_source_contribution_id": contribution.id,
                "x_validated": False,
            })

        # Audit trail on the task
        task.message_post(
            body=_(
                "<strong>Portal submission</strong> by "
                "%(user)s, credited to %(credit)s — pending "
                "Secretary review.<br/>"
                "%(prog)s — %(date)s · %(elks)s Elks / %(helpers)s "
                "Helpers · %(hrs)s hrs · $%(cash)s cash / $%(nc)s "
                "in-kind"
            ) % {
                "user": request.env.user.name,
                "credit": credit_user.name,
                "prog": program_name,
                "date": event_date,
                "elks": _safe_int(post.get("elks_count")),
                "helpers": _safe_int(post.get("helper_count")),
                "hrs": elks_hours + _safe_float(post.get("helper_hours")),
                "cash": _safe_float(post.get("cash_value")),
                "nc": _safe_float(post.get("non_cash_value")),
            },
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # Redirect to the list with a flash message
        return request.redirect(
            "/my/charity?submitted=1&entry_id=%s" % contribution.id
        )
