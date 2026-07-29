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
        "(E) Elk Hours",
        help="Total Elk hours for the activity.  If 'per-person' is "
             "checked below, enter hours PER Elk and we multiply by "
             "# Elks; otherwise enter the summed total (e.g. 6 Elks × "
             "6 hrs = 36).  Whole hours OK.",
    )
    helper_hours = fields.Float(
        "(F) Helper Hours",
        help="Same rule as Elk Hours — per-person if the checkbox is "
             "on, otherwise a pre-summed total.",
    )

    # ── (G)–(H) Mileage ───────────────────────────────────────────
    elks_miles = fields.Float(
        "(G) Elk Miles",
        help="ROUND TRIP miles for Elks.  If 'per-person' is checked, "
             "enter miles PER Elk (we multiply); otherwise total "
             "miles = people × distance × 2 (Column G).",
    )
    helper_miles = fields.Float(
        "(H) Helper Miles",
        help="Same rule as Elk Miles.",
    )

    # ── Per-person multiplier toggle ─────────────────────────────
    # Volunteers often think "I worked 2 hours, and 3 of us did it"
    # rather than "we put in 6 volunteer-hours total".  Flipping this
    # switch lets them enter the natural way and the wizard does the
    # arithmetic.  The stored contribution ALWAYS holds the summed
    # totals (that's what the GL report expects), so the toggle only
    # affects wizard input semantics, not what elks.org sees.
    hours_per_person = fields.Boolean(
        "Hours & Miles are Per-Person (multiply by count)",
        default=False,
        help="When checked, the Hours and Miles fields are treated as "
             "PER ELK and PER HELPER, then multiplied by # Elks (C) "
             "and # Helpers (D) automatically.  Preview below shows "
             "the resulting totals that get saved and reported.",
    )

    # Computed preview so the user sees exactly what will be stored
    # BEFORE clicking Submit.
    effective_elks_hours = fields.Float(
        "→ Total Elk Hours (saved)", compute="_compute_effective",
        help="Preview of what gets saved to the contribution.",
    )
    effective_helper_hours = fields.Float(
        "→ Total Helper Hours (saved)", compute="_compute_effective",
    )
    effective_elks_miles = fields.Float(
        "→ Total Elk Miles (saved)", compute="_compute_effective",
    )
    effective_helper_miles = fields.Float(
        "→ Total Helper Miles (saved)", compute="_compute_effective",
    )

    @api.depends("hours_per_person",
                 "elks_hours", "helper_hours",
                 "elks_miles", "helper_miles",
                 "elks_count", "helper_count",
                 "elks_member_ids", "helper_member_ids",
                 "task_id", "charity_category_id", "event_date")
    def _compute_effective(self):
        """Compute the totals that will be saved on the contribution.

        Base rule: per-person mode multiplies by count; total mode
        stores as-typed.

        Attendance-override refinement (per-person mode only): if any
        of the attributed members have matching validated attendance
        for (task, event_date), REPLACE their per-person value with
        their actual attendance hours + miles.  The rest still get
        the per-person value.  Non-attributed people still get the
        per-person value × count.

        Example: 4 Elks entered as 2 hrs each. Alice has a 2.5-hr
        attendance record for this task+date. Result:
          Alice     = 2.5 (from attendance)
          Bob/C/D   = 2   (per-person entry)
          Total     = 8.5 (was 8.0 without override)
        """
        for w in self:
            if not w.hours_per_person:
                w.effective_elks_hours = w.elks_hours or 0.0
                w.effective_helper_hours = w.helper_hours or 0.0
                w.effective_elks_miles = w.elks_miles or 0.0
                w.effective_helper_miles = w.helper_miles or 0.0
                continue

            # Per-person mode.  Use the resolved task (task_id OR
            # the auto-lookup by category) so the override works even
            # when the user hasn't picked an existing activity.
            resolved_task = w._resolve_effective_task()
            att_elks = w._lookup_attendance_overrides(
                w.elks_member_ids, w.event_date, resolved_task,
            )
            att_help = w._lookup_attendance_overrides(
                w.helper_member_ids, w.event_date, resolved_task,
            )

            per_elk_h = w.elks_hours or 0.0
            per_elk_m = w.elks_miles or 0.0
            per_hlp_h = w.helper_hours or 0.0
            per_hlp_m = w.helper_miles or 0.0

            # Attributed members: per-person value OR their attendance.
            attrib_elk_h = sum(
                att_elks[e.id]["hours"] if e.id in att_elks else per_elk_h
                for e in w.elks_member_ids
            )
            attrib_elk_m = sum(
                att_elks[e.id]["miles"] if e.id in att_elks else per_elk_m
                for e in w.elks_member_ids
            )
            attrib_hlp_h = sum(
                att_help[e.id]["hours"] if e.id in att_help else per_hlp_h
                for e in w.helper_member_ids
            )
            attrib_hlp_m = sum(
                att_help[e.id]["miles"] if e.id in att_help else per_hlp_m
                for e in w.helper_member_ids
            )

            # Non-attributed count: (declared count) minus (attributed).
            extra_ne = max((w.elks_count or 0) - len(w.elks_member_ids), 0)
            extra_nh = max((w.helper_count or 0) - len(w.helper_member_ids), 0)

            w.effective_elks_hours = attrib_elk_h + per_elk_h * extra_ne
            w.effective_helper_hours = attrib_hlp_h + per_hlp_h * extra_nh
            w.effective_elks_miles = attrib_elk_m + per_elk_m * extra_ne
            w.effective_helper_miles = attrib_hlp_m + per_hlp_m * extra_nh

    def _resolve_effective_task(self):
        """Return the task the wizard would submit against — the
        explicit task_id if picked, otherwise the auto-lookup by
        category + lodge year that action_submit does.

        Used by the attendance-override preview so the Secretary sees
        overrides BEFORE hitting Submit, not just at save time."""
        self.ensure_one()
        if self.task_id:
            return self.task_id
        if not self.charity_category_id or not self.event_date:
            return self.env["project.task"]
        Project = self.env["project.project"].sudo()
        lodge_year = self._lodge_year_for_date(self.event_date)
        proj = Project.search([
            ("x_is_charity_parent", "=", True),
            ("x_lodge_year", "=", lodge_year),
        ], limit=1)
        if not proj:
            return self.env["project.task"]
        return self.env["project.task"].sudo().search([
            ("project_id", "=", proj.id),
            ("x_charity_category_id", "=", self.charity_category_id.id),
        ], limit=1)

    def _lookup_attendance_overrides(self, employees, event_date, task):
        """Return {employee_id: {hours, miles}} for members whose
        validated attendance for (task, event_date) should override
        the per-person defaults.

        Empty dict if any argument is missing — the wizard falls back
        to the per-person values.

        Date matching is timezone-safe: we convert stored UTC
        check_in/check_out to the wizard user's local timezone before
        comparing to event_date.  This handles evening events that
        straddle midnight UTC.
        """
        if not employees or not event_date or not task:
            return {}
        Att = self.env["hr.attendance"].sudo()
        # Search a WIDE range (event date ± 1 day UTC) then filter in
        # Python on the user-local date so we catch check-ins whose
        # UTC date differs from their local date.
        from datetime import datetime, time, timedelta
        window_start = datetime.combine(
            event_date - timedelta(days=1), time.min
        )
        window_end = datetime.combine(
            event_date + timedelta(days=2), time.min
        )
        atts = Att.search([
            ("employee_id", "in", employees.ids),
            ("x_charity_task_id", "=", task.id),
            ("x_validated", "=", True),
            ("check_out", "!=", False),
            ("check_in", ">=", fields.Datetime.to_string(window_start)),
            ("check_in", "<", fields.Datetime.to_string(window_end)),
        ])
        # Timezone-safe filter — accept if either the local check_in
        # date OR the local check_out date matches the event date.
        def _local_date(dt):
            if not dt:
                return None
            local = fields.Datetime.context_timestamp(self.env.user, dt)
            return local.date()

        out = {}
        for a in atts:
            if (_local_date(a.check_in) == event_date
                    or _local_date(a.check_out) == event_date):
                eid = a.employee_id.id
                hrs = a.x_charity_hours or a.worked_hours or 0.0
                mi = a.x_miles or 0.0
                if eid in out:
                    out[eid]["hours"] += hrs
                    out[eid]["miles"] += mi
                else:
                    out[eid] = {"hours": hrs, "miles": mi}
        return out

    def _diagnose_attendance_lookup(self, employees, event_date, task):
        """Return an HTML-formatted list of reasons why an attendance
        override wasn't found for each attributed member.  Called by
        the preview panel when the plain lookup returned empty."""
        if not employees:
            return ""
        if not event_date:
            return "<em>Enter an Event Date first.</em>"
        if not task:
            return (
                "<em>No matching charity Task found — "
                "either pick <strong>Existing Activity</strong> above "
                "or make sure a task for this category already exists "
                "in this lodge year's charity project.</em>"
            )
        Att = self.env["hr.attendance"].sudo()
        # Every attendance for these employees on this task, no other filters.
        atts = Att.search([
            ("employee_id", "in", employees.ids),
            ("x_charity_task_id", "=", task.id),
        ])
        lines = []
        for emp in employees:
            emp_atts = atts.filtered(lambda a: a.employee_id.id == emp.id)
            if not emp_atts:
                lines.append(
                    "<li><strong>%s</strong> — no attendance found for "
                    "task <em>%s</em>. Was the attendance tagged to "
                    "the right charity activity?</li>"
                    % (emp.name, task.display_name)
                )
                continue
            unvalidated = emp_atts.filtered(lambda a: not a.x_validated)
            no_checkout = emp_atts.filtered(lambda a: not a.check_out)
            if unvalidated:
                lines.append(
                    "<li><strong>%s</strong> — %d attendance row(s) "
                    "for this task but NOT VALIDATED by a Secretary "
                    "yet.  Validate them in <em>Hours → Hours "
                    "Awaiting Validation</em> and try again.</li>"
                    % (emp.name, len(unvalidated))
                )
            elif no_checkout:
                lines.append(
                    "<li><strong>%s</strong> — attendance is missing "
                    "a check-out time.</li>" % emp.name
                )
            else:
                lines.append(
                    "<li><strong>%s</strong> — attendance exists and "
                    "is validated, but its date doesn't match the "
                    "Event Date (%s).</li>"
                    % (emp.name, event_date)
                )
        return "<ul style='margin:4px 0 0 0;padding-left:20px;'>%s</ul>" % (
            "".join(lines)
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
    attendance_override_summary = fields.Html(
        "Attendance Override Preview",
        compute="_compute_attendance_override_summary",
        help="Live preview of attributed members whose attendance for "
             "this (task, date) will replace their per-person value.",
    )

    @api.depends("elks_member_ids", "helper_member_ids",
                 "task_id", "charity_category_id", "event_date",
                 "hours_per_person")
    def _compute_attendance_override_summary(self):
        for w in self:
            if not w.hours_per_person:
                w.attendance_override_summary = False
                continue
            resolved_task = w._resolve_effective_task()
            all_members = w.elks_member_ids | w.helper_member_ids
            if not all_members:
                w.attendance_override_summary = False
                continue

            att_e = w._lookup_attendance_overrides(
                w.elks_member_ids, w.event_date, resolved_task,
            )
            att_h = w._lookup_attendance_overrides(
                w.helper_member_ids, w.event_date, resolved_task,
            )
            if not att_e and not att_h:
                # No matches — show diagnostic so Secretary knows why.
                diag = w._diagnose_attendance_lookup(
                    all_members, w.event_date, resolved_task,
                )
                w.attendance_override_summary = (
                    "<div class='alert alert-warning' "
                    "style='padding:6px 10px;font-size:12px;"
                    "margin-bottom:0;'>"
                    "<strong>No attendance overrides found — "
                    "every attributed member will use the per-person "
                    "value entered above.</strong>%s</div>"
                    % diag
                )
                continue

            rows = []
            for e in w.elks_member_ids:
                if e.id in att_e:
                    rows.append(
                        "<li><strong>%s</strong> (Elk) — "
                        "<span style='color:#198754;'>%.2f hrs "
                        "from attendance</span> (overrides %.2f)</li>"
                        % (e.name, att_e[e.id]["hours"], w.elks_hours or 0)
                    )
            for e in w.helper_member_ids:
                if e.id in att_h:
                    rows.append(
                        "<li><strong>%s</strong> (Helper) — "
                        "<span style='color:#198754;'>%.2f hrs "
                        "from attendance</span> (overrides %.2f)</li>"
                        % (e.name, att_h[e.id]["hours"], w.helper_hours or 0)
                    )
            w.attendance_override_summary = (
                "<div class='alert alert-info' style='padding:6px 10px;"
                "font-size:12px;margin-bottom:0;'>"
                "<strong>%d attendance override(s) will apply:</strong>"
                "<ul style='margin:4px 0 0 0;padding-left:20px;'>%s</ul>"
                "</div>" % (len(rows), "".join(rows))
            )

    @api.depends("elks_member_ids", "helper_member_ids",
                 "effective_elks_hours", "effective_helper_hours")
    def _compute_per_member(self):
        # Split the EFFECTIVE (post-multiplier) totals across the
        # selected members.  This keeps the "Each Elk receives …"
        # display honest whether the checkbox is on or off.
        for w in self:
            ne = len(w.elks_member_ids)
            nh = len(w.helper_member_ids)
            w.per_elk_hours = (
                (w.effective_elks_hours / ne) if ne else 0.0
            )
            w.per_helper_hours = (
                (w.effective_helper_hours / nh) if nh else 0.0
            )

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
        # Always store the SUMMED totals — this is what the GL report
        # and elks.org auto-push consume.  effective_* is the same as
        # elks_hours/etc. when the per-person toggle is off, or those
        # values multiplied by the count when it's on.
        contribution = self.env["elks.charity.contribution"].sudo().create({
            "name": contribution_name,
            "contribution_date": self.event_date,
            "contribution_type": "other",
            "task_id": task.id,
            "head_count": self.head_count,
            "elks_count": self.elks_count,
            "helper_count": self.helper_count,
            "elks_hours": self.effective_elks_hours,
            "helper_hours": self.effective_helper_hours,
            "elks_miles": self.effective_elks_miles,
            "helper_miles": self.effective_helper_miles,
            "cash_value": self.cash_value,
            "non_cash_value": self.non_cash_value,
            "currency_id": self.currency_id.id,
            "recipient_org": self.committee or False,
            "state": "confirmed",
            "submitted_by": self.env.user.id,
            "confirmed_by": self.env.user.id,
            "confirmed_date": fields.Datetime.now(),
            "x_source": "quick_entry",
        })

        # 4. Per-member personal-record lines (excluded from GL).
        #    Per-member hours:
        #      - if hours_per_person AND matching attendance exists →
        #        use attendance hours (this is the "attendance
        #        override" the Secretary set up)
        #      - if hours_per_person AND no attendance → use the raw
        #        per-person value (elks_hours / helper_hours)
        #      - if NOT hours_per_person → split effective total evenly
        #        across attributed members (legacy behaviour)
        AAL = self.env["account.analytic.line"].sudo()
        lines_created = 0

        # Build the attendance-override lookup ONCE so the loops don't
        # each hit the DB.  Uses the ACTUAL task we just created /
        # reused (not the resolved lookup) — at this point they are
        # the same, but this is more correct if action_submit's task
        # selection ever diverges from _resolve_effective_task.
        att_e = (
            self._lookup_attendance_overrides(
                self.elks_member_ids, self.event_date, task,
            ) if self.hours_per_person else {}
        )
        att_h = (
            self._lookup_attendance_overrides(
                self.helper_member_ids, self.event_date, task,
            ) if self.hours_per_person else {}
        )

        if self.elks_member_ids and self.effective_elks_hours:
            for emp in self.elks_member_ids:
                if self.hours_per_person:
                    if emp.id in att_e:
                        per_h = att_e[emp.id]["hours"]
                        per_m = att_e[emp.id]["miles"]
                    else:
                        per_h = self.elks_hours or 0.0
                        per_m = self.elks_miles or 0.0
                else:
                    per_h = (
                        self.effective_elks_hours
                        / len(self.elks_member_ids)
                    )
                    per_m = (
                        (self.effective_elks_miles or 0.0)
                        / len(self.elks_member_ids)
                    )
                AAL.create(self._personal_line_vals(
                    emp, task, per_h, per_m,
                    is_helper=False, contribution=contribution,
                ))
                lines_created += 1
        if self.helper_member_ids and self.effective_helper_hours:
            for emp in self.helper_member_ids:
                if self.hours_per_person:
                    if emp.id in att_h:
                        per_h = att_h[emp.id]["hours"]
                        per_m = att_h[emp.id]["miles"]
                    else:
                        per_h = self.helper_hours or 0.0
                        per_m = self.helper_miles or 0.0
                else:
                    per_h = (
                        self.effective_helper_hours
                        / len(self.helper_member_ids)
                    )
                    per_m = (
                        (self.effective_helper_miles or 0.0)
                        / len(self.helper_member_ids)
                    )
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
                "elksh": self.effective_elks_hours,
                "helph": self.effective_helper_hours,
                "elksm": self.effective_elks_miles,
                "helpm": self.effective_helper_miles,
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
