# -*- coding: utf-8 -*-
"""Quick Entry wizard — mirrors the BPOE "Data Collection Survey of
Volunteer, Youth, Charitable and Community Service Programs" paper form
and the elks.org website entry page.

Field layout matches the official form one-to-one:
    Committee      → committee (free text)
    Date           → event_date
    (A) Program    → program_name
    (B) Participants  → head_count
    (C) # Elks     → elks_count
    (D) # Helpers  → helper_count
    (E) Elk Hours  → elks_hours
    (F) Helper Hrs → helper_hours
    (G) Elk Miles  → elks_miles
    (H) Helper Mi  → helper_miles
    (I) Non-Cash   → non_cash_value
    (J) Cash       → cash_value

On submit:
    1. Find / create the charity-parent project for the lodge year
       containing event_date.
    2. Find / create the task linked to the selected GL category
       (one task per category per project; the program_name updates
       the task description if provided).
    3. Create ONE elks.charity.contribution (state=confirmed) carrying
       the full bulk totals — this is what the Grand Lodge report
       sums.
    4. If individual Elks/Helpers were selected, create a
       account.analytic.line per member with the hours split evenly
       and  x_personal_record=True .  Those lines show on each
       member's personal charity history but are EXCLUDED from GL
       totals (the contribution already has them).
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class ElksCharityQuickEntryWizard(models.TransientModel):
    _name = "elks.charity.quick.entry.wizard"
    _description = "Quick Entry — BPOE Data Collection Survey"

    # ── header ───────────────────────────────────────────────────
    committee = fields.Char(
        "Committee",
        help="Optional — name of the lodge committee submitting this "
             "entry (e.g. \"Veterans' Service Committee\").",
    )
    event_date = fields.Date(
        "Date", required=True,
        default=fields.Date.context_today,
        help="Date the activity took place.  Determines which lodge "
             "year the entry rolls into.",
    )

    # ── (A) Program identity ─────────────────────────────────────
    charity_category_id = fields.Many2one(
        "elks.charity.category", string="GL Category", required=True,
        help="Pick the Grand Lodge category 1001-9999 that best matches "
             "this activity (Section + numbered category from the GL "
             "Charity Workbook).",
    )
    program_name = fields.Char(
        "(A) Program", required=True,
        help="Brief description of the program (Column A on the GL "
             "form).  E.g. \"Bicycle Safety Day\", \"Veterans Stand "
             "Down Event\".",
    )
    task_id = fields.Many2one(
        "project.task", string="Existing Activity (optional)",
        domain="[('x_is_charity_activity', '=', True)]",
        help="If this entry belongs to an existing charity activity, "
             "pick it here.  Otherwise leave blank and the wizard will "
             "create or reuse the auto-task for the selected category.",
    )

    # ── (B)–(D) People counts ─────────────────────────────────────
    head_count = fields.Integer(
        "(B) Number of Participants",
        help="Total people who BENEFITED from the program (Column B).  "
             "Count individuals — not couples, teams or groups.",
    )
    elks_count = fields.Integer(
        "(C) Number of Elks",
        help="Number of Elks who put on the program (Column C).  "
             "Count individuals — not couples, teams or groups.",
    )
    helper_count = fields.Integer(
        "(D) Number of Helpers",
        help="Number of non-Elk helpers (Column D).  Count individuals.",
    )

    # ── (E)–(F) Volunteer hours ───────────────────────────────────
    elks_hours = fields.Float(
        "(E) Total Elk Hours",
        help="Per the GL Workbook: 6 Elks × 6 hours = 36 (Column E).  "
             "Whole hours only.",
    )
    helper_hours = fields.Float(
        "(F) Total Helper Hours",
        help="Same arithmetic for non-Elk helpers (Column F).",
    )

    # ── (G)–(H) Mileage ───────────────────────────────────────────
    elks_miles = fields.Float(
        "(G) Elk Miles",
        help="ROUND TRIP miles for Elks.  People × distance × round "
             "trip (Column G).",
    )
    helper_miles = fields.Float(
        "(H) Helper Miles",
        help="Round-trip miles for helpers (Column H).",
    )

    # ── (I)–(J) Donations ─────────────────────────────────────────
    non_cash_value = fields.Monetary(
        "(I) Non-Cash Contributions",
        currency_field="currency_id",
        help="Cash VALUE of in-kind contributions: hall donations, "
             "food, band, clothing, bingo, parties, gifts, eyeglasses "
             "(Column I).  Don't include hours or mileage.  Whole "
             "dollars only.",
    )
    cash_value = fields.Monetary(
        "(J) Cash Donations",
        currency_field="currency_id",
        help="Actual cash, checks, money orders, or purchase value of "
             "savings bonds donated (Column J).  Whole dollars only.",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    # ── Per-member attribution (optional) ────────────────────────
    elks_member_ids = fields.Many2many(
        "hr.employee",
        "elks_charity_qew_elks_rel", "wizard_id", "employee_id",
        string="Attribute Elk Hours To",
        domain="[]",
        help="Optional — pick Elk members whose personal charity "
             "history should reflect this event.  Total Elk Hours (E) "
             "and Elk Miles (G) are SPLIT EVENLY across the people you "
             "pick.  The bulk totals stay on the contribution for the "
             "GL report; the per-member lines are personal records "
             "only and don't re-count toward GL.",
    )
    helper_member_ids = fields.Many2many(
        "hr.employee",
        "elks_charity_qew_helpers_rel", "wizard_id", "employee_id",
        string="Attribute Helper Hours To",
        domain="[]",
        help="Same as above for non-Elk helpers.",
    )

    # ── Convenience computed display ─────────────────────────────
    per_elk_hours = fields.Float(
        "Per-Elk Hours (split)", compute="_compute_per_member",
        help="Hours each selected Elk member will receive on their "
             "personal record.",
    )
    per_helper_hours = fields.Float(
        "Per-Helper Hours (split)", compute="_compute_per_member",
    )

    @api.depends("elks_member_ids", "helper_member_ids",
                 "elks_hours", "helper_hours")
    def _compute_per_member(self):
        for w in self:
            ne = len(w.elks_member_ids)
            nh = len(w.helper_member_ids)
            w.per_elk_hours = (w.elks_hours / ne) if ne else 0.0
            w.per_helper_hours = (w.helper_hours / nh) if nh else 0.0

    # ── Validation ───────────────────────────────────────────────
    @api.constrains("elks_hours", "helper_hours",
                    "elks_miles", "helper_miles",
                    "cash_value", "non_cash_value",
                    "head_count", "elks_count", "helper_count")
    def _check_non_negative(self):
        for w in self:
            negatives = [
                ("Elk Hours", w.elks_hours),
                ("Helper Hours", w.helper_hours),
                ("Elk Miles", w.elks_miles),
                ("Helper Miles", w.helper_miles),
                ("Cash", w.cash_value),
                ("Non-Cash", w.non_cash_value),
                ("Head Count", w.head_count),
                ("# Elks", w.elks_count),
                ("# Helpers", w.helper_count),
            ]
            for label, val in negatives:
                if (val or 0) < 0:
                    raise ValidationError(_(
                        "%s cannot be negative.", label
                    ))

    # ── Submit ───────────────────────────────────────────────────
    def action_submit(self):
        self.ensure_one()

        # 1. Locate / create the charity-parent project for the
        #    lodge year that contains event_date.
        Project = self.env["project.project"]
        lodge_year = self._lodge_year_for_date(self.event_date)
        project = Project.sudo().create_charity_parent_project(
            lodge_year=lodge_year
        )
        if project.x_is_closed:
            raise UserError(_(
                "Lodge year %s is closed — Quick Entry is locked.  "
                "Re-open the year or pick a date in the current year."
            ) % lodge_year)

        # 2. Locate / create the task.
        Task = self.env["project.task"]
        task = self.task_id
        if not task:
            task = Task.search([
                ("project_id", "=", project.id),
                ("x_charity_category_id", "=", self.charity_category_id.id),
            ], limit=1)
        if not task:
            task = Task.create({
                "name": self.program_name or self.charity_category_id.name,
                "project_id": project.id,
                "x_charity_category_id": self.charity_category_id.id,
                "x_event_date": self.event_date,
                "x_head_count": self.head_count,
            })
        else:
            # Refresh the event date + head count on the existing task
            # so the dashboard / reports reflect this entry.
            vals = {}
            if self.event_date and not task.x_event_date:
                vals["x_event_date"] = self.event_date
            if self.head_count:
                vals["x_head_count"] = (
                    (task.x_head_count or 0) + self.head_count
                )
            if vals:
                task.write(vals)

        # 3. Create ONE contribution carrying the full bulk totals
        #    (this is what the GL report sums).
        contribution_name = self.program_name or self.charity_category_id.name
        if self.committee:
            contribution_name = f"{contribution_name} — {self.committee}"
        contribution = self.env["elks.charity.contribution"].sudo().create({
            "name": contribution_name,
            "contribution_date": self.event_date,
            "contribution_type": "other",
            "task_id": task.id,
            "head_count": self.head_count,
            "elks_count": self.elks_count,
            "helper_count": self.helper_count,
            "elks_hours": self.elks_hours,
            "helper_hours": self.helper_hours,
            "elks_miles": self.elks_miles,
            "helper_miles": self.helper_miles,
            "cash_value": self.cash_value,
            "non_cash_value": self.non_cash_value,
            "currency_id": self.currency_id.id,
            "recipient_org": self.committee or False,
            "state": "confirmed",
            "submitted_by": self.env.user.id,
            "confirmed_by": self.env.user.id,
            "confirmed_date": fields.Datetime.now(),
        })

        # 4. Per-member personal-record lines (excluded from GL).
        AAL = self.env["account.analytic.line"].sudo()
        lines_created = 0
        if self.elks_member_ids and self.elks_hours:
            per_h = self.elks_hours / len(self.elks_member_ids)
            per_m = (self.elks_miles or 0.0) / len(self.elks_member_ids)
            for emp in self.elks_member_ids:
                AAL.create(self._personal_line_vals(
                    emp, task, per_h, per_m,
                    is_helper=False, contribution=contribution,
                ))
                lines_created += 1
        if self.helper_member_ids and self.helper_hours:
            per_h = self.helper_hours / len(self.helper_member_ids)
            per_m = (self.helper_miles or 0.0) / len(self.helper_member_ids)
            for emp in self.helper_member_ids:
                AAL.create(self._personal_line_vals(
                    emp, task, per_h, per_m,
                    is_helper=True, contribution=contribution,
                ))
                lines_created += 1

        # 5. Friendly chatter post on the task so the audit trail is
        #    visible to anyone opening the activity.
        task.message_post(
            body=_(
                "<strong>Quick Entry submitted</strong><br/>"
                "%(prog)s — %(date)s<br/>"
                "Bulk: %(elks)d Elks / %(helpers)d Helpers · "
                "%(elksh).0f / %(helph).0f hrs · "
                "%(elksm).0f / %(helpm).0f miles · "
                "$%(cash).0f cash / $%(nc).0f in-kind<br/>"
                "Personal records: %(n_lines)d line(s)."
            ) % {
                "prog": contribution_name,
                "date": self.event_date,
                "elks": self.elks_count,
                "helpers": self.helper_count,
                "elksh": self.elks_hours,
                "helph": self.helper_hours,
                "elksm": self.elks_miles,
                "helpm": self.helper_miles,
                "cash": self.cash_value,
                "nc": self.non_cash_value,
                "n_lines": lines_created,
            },
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

        # 6. Return user to the contribution so they can confirm
        #    everything saved as expected.
        return {
            "type": "ir.actions.act_window",
            "name": _("Quick Entry — Bulk Contribution"),
            "res_model": "elks.charity.contribution",
            "res_id": contribution.id,
            "view_mode": "form",
            "target": "current",
        }

    # ── helpers ──────────────────────────────────────────────────
    @staticmethod
    def _lodge_year_for_date(d):
        if not d:
            return None
        # Lodge year = Apr 1 → Mar 31
        if d.month >= 4:
            return f"{d.year}-{d.year + 1}"
        return f"{d.year - 1}-{d.year}"

    def _personal_line_vals(self, employee, task, hours, miles,
                            is_helper, contribution):
        return {
            "name": (
                f"[Quick Entry] {self.program_name or task.name}"
            ),
            "date": self.event_date,
            "employee_id": employee.id,
            "project_id": task.project_id.id,
            "task_id": task.id,
            "unit_amount": hours,
            "x_miles": miles,
            "x_is_helper": is_helper,
            "x_personal_record": True,
            "x_source_contribution_id": contribution.id,
            "x_validated": False,  # personal records are NEVER validated
        }
