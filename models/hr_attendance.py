# -*- coding: utf-8 -*-
"""Lets an existing employee Attendance record (clock-in / clock-out)
be tagged as charity work, so the same hours that show up on the
employee's time card also count toward the Grand Lodge Charity Report.

IMPORTANT design note
---------------------
This module **never modifies an existing attendance record's
check_in / check_out / worked_hours fields**.  All charity-related
fields are pure metadata sitting alongside the original attendance
data, so installing or upgrading this module cannot change any
employee's recorded work time.

The Grand Lodge report and the project task roll-ups pull from BOTH
sources (timesheet lines AND tagged attendance records), de-duped by
employee + date + task so a clock-in tagged as charity does not get
double-counted if a timesheet line also exists for the same activity.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    # --- Charity tagging (all optional, all metadata) ---
    x_charity_task_id = fields.Many2one(
        "project.task", string="Charity Activity",
        domain="[('x_is_charity_activity', '=', True)]",
        help="Tag this attendance entry as time spent on a specific "
             "charity activity.  Counts toward the Grand Lodge Charity "
             "Report once validated.  Leave empty for non-charity work.",
    )
    x_charity_hours = fields.Float(
        "Charity Hours",
        help="Hours to count toward the charity report.  Defaults to the "
             "raw clock-in → clock-out duration when a Charity Activity "
             "is selected; override if only part of the shift was charity "
             "work.\n\n"
             "Per the GL Workbook: enter the TOTAL elapsed time for the "
             "group.  If 6 Elks worked as a group for 3 hours, total Elk "
             "Hours is 18 (6 × 3).  Round to WHOLE hours — no fractions "
             "or decimals.",
    )
    x_charity_category_id = fields.Many2one(
        "elks.charity.category",
        related="x_charity_task_id.x_charity_category_id",
        store=True, readonly=True, string="GL Category",
    )
    x_is_charity_attendance = fields.Boolean(
        "Is Charity Time", compute="_compute_is_charity",
        store=True, index=True,
    )
    x_is_helper = fields.Boolean(
        "Non-Elk Helper", default=False,
        help="Check if these are non-Elk volunteer hours (rare for "
             "employees, common when guests clock in for a charity event).",
    )
    x_miles = fields.Float(
        "Miles (Round Trip)", default=0.0,
        help="Total miles driven for this activity, ROUND TRIP.  Per the "
             "GL Workbook: enter people × distance × round trip.  Round "
             "to WHOLE miles — no fractions or decimals.",
    )
    x_cash_value = fields.Monetary(
        "Cash Donated", currency_field='x_currency_id',
        help="Cash, check, or money order donated.  Per the GL Workbook: "
             "WHOLE DOLLARS only — no dollar signs, cents, or decimals.  "
             "For U.S. Savings Bonds, use purchase value, not maturity.",
    )
    x_non_cash_value = fields.Monetary(
        "Non-Cash Value", currency_field='x_currency_id',
        help="Fair market value of donated goods (refreshments, supplies, "
             "door prizes, postage, donated clothing, etc.).  Per the GL "
             "Workbook: WHOLE DOLLARS only.  Use IRS valuation guidelines "
             "for used items if unsure.",
    )
    x_charity_notes = fields.Text("Charity Notes")
    x_validated = fields.Boolean(
        "Validated for GL Report", default=True, tracking=True,
        help="Auto-set to True the moment a charity task is attached "
             "(19.0.6.5+).  Was manually gated in earlier versions "
             "when the Secretary had to check off 'yes, I entered "
             "this on elks.org' — the Chrome extension makes that "
             "redundant.  Uncheck manually only if you need to "
             "exclude this row from GL totals for some reason.",
    )
    x_validated_by = fields.Many2one(
        "res.users", string="Validated By", readonly=True, copy=False,
    )
    x_validated_on = fields.Date(
        "Validated On", readonly=True, copy=False,
    )
    x_currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )

    @api.depends("x_charity_task_id")
    def _compute_is_charity(self):
        for rec in self:
            rec.x_is_charity_attendance = bool(rec.x_charity_task_id)

    @api.constrains("x_charity_task_id")
    def _check_charity_project_not_closed(self):
        """Prevent tagging attendance to a closed charity project."""
        for rec in self:
            if not rec.x_charity_task_id:
                continue
            proj = rec.x_charity_task_id.project_id
            if proj and proj.x_is_charity_parent and proj.x_is_closed:
                raise UserError(_(
                    "Cannot tag attendance to closed charity project "
                    "'%(project)s'.  The lodge year has been wrapped up."
                ) % {'project': proj.name})

    @api.onchange("x_charity_task_id", "check_in", "check_out")
    def _onchange_charity_task(self):
        """Default charity hours to the raw clock-in → clock-out duration
        when a charity task is selected.

        We deliberately do NOT use ``worked_hours`` here — Odoo computes
        that field by subtracting unpaid breaks defined in the employee's
        resource calendar (e.g. lunch).  Volunteers don't take unpaid
        lunch breaks while volunteering, so 11 AM → 3 PM should be 4 h
        on the GL report, not 3 h.
        """
        if self.x_charity_task_id and not self.x_charity_hours:
            self.x_charity_hours = self._compute_raw_charity_hours()

    def _compute_raw_charity_hours(self):
        """Return the raw clock-in to clock-out duration in hours,
        with no break/lunch deduction.  Used as the default for
        ``x_charity_hours`` and as the fallback when that field is
        not explicitly set on a validated attendance record."""
        self.ensure_one()
        if self.check_in and self.check_out:
            delta = self.check_out - self.check_in
            return delta.total_seconds() / 3600.0
        return 0.0

    def action_reset_charity_hours_to_raw(self):
        """Recompute ``x_charity_hours`` from clock-in/clock-out for
        every record in self.  Use this to fix historical attendance
        rows that got the old worked_hours-based default."""
        for rec in self:
            if rec.x_charity_task_id:
                rec.x_charity_hours = rec._compute_raw_charity_hours()

    # ------------------------------------------------------------------
    # Validation actions
    # ------------------------------------------------------------------
    def action_validate_charity_attendance(self):
        for rec in self:
            if not rec.x_charity_task_id:
                continue
            rec.write({
                'x_validated': True,
                'x_validated_by': self.env.user.id,
                'x_validated_on': fields.Date.context_today(self),
            })

    def action_unvalidate_charity_attendance(self):
        self.write({
            'x_validated': False,
            'x_validated_by': False,
            'x_validated_on': False,
        })

    # ------------------------------------------------------------------
    # Invalidate charity task totals when attendance records change
    # ------------------------------------------------------------------
    _CHARITY_TRIGGER_FIELDS = {
        'x_charity_task_id', 'x_charity_hours', 'x_validated',
        'x_is_helper', 'x_miles', 'x_cash_value', 'x_non_cash_value',
        'worked_hours', 'check_in', 'check_out',
    }

    def _invalidate_charity_tasks(self, task_ids=None):
        """Force recompute of totals on linked charity tasks."""
        if task_ids is None:
            task_ids = self.mapped('x_charity_task_id')
        if task_ids:
            task_ids._compute_totals()

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        charity_records = records.filtered('x_charity_task_id')
        if charity_records:
            # Auto-validate every charity attendance row on create so
            # hours count immediately.  Prior versions gated on a
            # manual click — see field docstring.
            charity_records._auto_validate_charity()
            # Attendance is authoritative — remove any Quick Entry
            # personal-record line that covers the same person + task
            # + date, and reduce the parent contribution's declared
            # hours by the removed PR share.  See _reconcile_pr_lines.
            charity_records._reconcile_pr_lines()
            # Ensure the (task, date) has a Confirmed contribution so
            # it enters the elks.org push queue.  If Quick Entry
            # already created one, we update it in _reconcile_pr_lines
            # above; if there's no contribution yet, this creates one
            # from the attendance data.
            charity_records._ensure_attendance_contribution()
            charity_records._invalidate_charity_tasks()
        return records

    def write(self, vals):
        # Collect tasks BEFORE write (in case x_charity_task_id changes)
        old_tasks = self.env['project.task']
        if self._CHARITY_TRIGGER_FIELDS & set(vals):
            old_tasks = self.mapped('x_charity_task_id')
        res = super().write(vals)
        # If someone JUST attached a charity task to an existing
        # attendance row, auto-validate the same as we do on create.
        if 'x_charity_task_id' in vals and vals.get('x_charity_task_id'):
            newly_tagged = self.filtered('x_charity_task_id')
            newly_tagged._auto_validate_charity()
            newly_tagged._reconcile_pr_lines()
            newly_tagged._ensure_attendance_contribution()
        elif ('check_in' in vals or 'x_charity_hours' in vals or
              'x_miles' in vals or 'x_is_helper' in vals) and (
                self._CHARITY_TRIGGER_FIELDS & set(vals)):
            # Attendance details changed — refresh derived data.
            still_charity = self.filtered('x_charity_task_id')
            still_charity._reconcile_pr_lines()
            still_charity._ensure_attendance_contribution()
        if self._CHARITY_TRIGGER_FIELDS & set(vals):
            new_tasks = self.mapped('x_charity_task_id')
            self._invalidate_charity_tasks(old_tasks | new_tasks)
        return res

    def _auto_validate_charity(self):
        """Flip x_validated=True on charity attendance rows that aren't
        already validated.  Idempotent."""
        needs_flip = self.filtered(
            lambda r: r.x_charity_task_id and not r.x_validated
        )
        if not needs_flip:
            return
        needs_flip.sudo().write({
            'x_validated': True,
            'x_validated_by': self.env.user.id,
            'x_validated_on': fields.Date.context_today(self),
        })

    def _reconcile_pr_lines(self):
        """Attendance is the authoritative source of truth for a
        person's charity hours.  When new charity attendance arrives
        for (employee, task, date) that already has a Quick Entry
        personal-record line for the same tuple:

          1. Delete the personal-record analytic line — it's stale
             the moment the real clock-in shows up.
          2. Reduce the parent contribution's declared elks/helper
             hours + miles by the removed PR share so the total that
             pushes to elks.org reflects reality (attendance replaces
             the estimate).
          3. Chatter-log the swap on the contribution so the audit
             trail is preserved.

        Idempotent: subsequent attendance edits find no PR line and
        no-op.  If attendance is later deleted, the contribution is
        NOT restored — the Secretary can re-attribute manually if
        that's really wanted.
        """
        AAL = self.env["account.analytic.line"].sudo()
        for att in self:
            if not att.x_charity_task_id or not att.check_in:
                continue
            att_date = att.check_in.date()
            matching_prs = AAL.search([
                ("task_id", "=", att.x_charity_task_id.id),
                ("employee_id", "=", att.employee_id.id),
                ("date", "=", att_date),
                ("x_personal_record", "=", True),
            ])
            if not matching_prs:
                continue
            # Group PR lines by parent contribution so we adjust each
            # contribution once with the total-of-shared-lines.
            contribs_touched = {}
            for pr in matching_prs:
                contrib = pr.x_source_contribution_id
                key = contrib.id if contrib else 0
                if key not in contribs_touched:
                    contribs_touched[key] = {
                        "contrib": contrib,
                        "elk_hours": 0.0,
                        "help_hours": 0.0,
                        "elk_miles": 0.0,
                        "help_miles": 0.0,
                        "employees": set(),
                    }
                bucket = contribs_touched[key]
                if pr.x_is_helper:
                    bucket["help_hours"] += pr.unit_amount
                    bucket["help_miles"] += pr.x_miles or 0.0
                else:
                    bucket["elk_hours"] += pr.unit_amount
                    bucket["elk_miles"] += pr.x_miles or 0.0
                bucket["employees"].add(pr.employee_id.name)

            for info in contribs_touched.values():
                contrib = info["contrib"]
                if contrib:
                    contrib.sudo().write({
                        "elks_hours": max(
                            0.0, (contrib.elks_hours or 0.0)
                                 - info["elk_hours"]),
                        "helper_hours": max(
                            0.0, (contrib.helper_hours or 0.0)
                                 - info["help_hours"]),
                        "elks_miles": max(
                            0.0, (contrib.elks_miles or 0.0)
                                 - info["elk_miles"]),
                        "helper_miles": max(
                            0.0, (contrib.helper_miles or 0.0)
                                 - info["help_miles"]),
                    })
                    contrib.message_post(
                        body=_(
                            "<strong>Attendance override applied.</strong>"
                            "<br/>%(who)s clocked in for this "
                            "activity on %(date)s — their personal-"
                            "record share (Elk hrs %(eh).2f · miles "
                            "%(em).2f · Helper hrs %(hh).2f · miles "
                            "%(hm).2f) has been removed from this "
                            "contribution's declared totals and "
                            "replaced by the actual attendance.",
                            who=", ".join(sorted(info["employees"])),
                            date=att_date,
                            eh=info["elk_hours"],
                            em=info["elk_miles"],
                            hh=info["help_hours"],
                            hm=info["help_miles"],
                        ),
                        subtype_xmlid="mail.mt_note",
                    )
            matching_prs.unlink()

    def _ensure_attendance_contribution(self):
        """For each (task, date) touched by self, ensure a Confirmed
        contribution exists so it enters the elks.org push queue.

        Rules:
          - If a Quick Entry contribution already covers the bucket,
            it stays as the authoritative record — attendance hours
            are added to it (Quick Entry declared totals + attendance
            actuals combined).  Ensures elks.org sees the true full
            picture, not just the Quick Entry declaration.
          - Otherwise, create a new Confirmed contribution with
            x_source='attendance' aggregating every validated
            charity attendance for that (task, date).
          - Never touch contributions in state 'cancelled' or that
            have already been pushed to elks.org (x_elks_org_state
            == 'pushed') — we don't want to retroactively edit rows
            that elks.org has already accepted.
        """
        Contrib = self.env["elks.charity.contribution"].sudo()
        Att = self.env["hr.attendance"].sudo()

        # Bucket the current recordset by (task, event_date) so we
        # touch each contribution once per save cycle.
        buckets = {}  # {(task_id, date): [attendance_id, ...]}
        for a in self:
            if not a.x_charity_task_id or not a.check_in:
                continue
            key = (a.x_charity_task_id.id, a.check_in.date())
            buckets.setdefault(key, [])
            buckets[key].append(a.id)

        # Cache the fallback "Categories Not Covered" category so any
        # attendance whose task lacks a category still generates a
        # pushable contribution.  Prior versions skipped these
        # silently, which is why lots of Lodge Operations rows were
        # missing from the push queue.
        fallback_cat = self.env.ref(
            "elkscharity.cat_9999", raise_if_not_found=False,
        )

        for (task_id, event_date), _ids in buckets.items():
            task = self.env["project.task"].browse(task_id)
            if not task.exists():
                continue
            if not task.x_charity_category_id and fallback_cat:
                task.sudo().x_charity_category_id = fallback_cat.id
            if not task.x_charity_category_id:
                # Still nothing — no fallback category exists, would
                # produce an unpushable contribution.  Skip.
                continue

            # Aggregate ALL validated attendance for this (task, date)
            # — not just the current record — so the contribution
            # totals stay in sync when multiple people clock in.
            same_bucket = Att.search([
                ("x_charity_task_id", "=", task_id),
                ("x_validated", "=", True),
                ("check_out", "!=", False),
            ]).filtered(
                lambda a: a.check_in and a.check_in.date() == event_date
            )
            if not same_bucket:
                continue

            elks_atts = same_bucket.filtered(lambda a: not a.x_is_helper)
            help_atts = same_bucket.filtered("x_is_helper")

            att_elks_hours = sum(
                a.x_charity_hours or (
                    (a.check_out - a.check_in).total_seconds() / 3600.0
                    if a.check_in and a.check_out else 0
                )
                for a in elks_atts
            )
            att_help_hours = sum(
                a.x_charity_hours or (
                    (a.check_out - a.check_in).total_seconds() / 3600.0
                    if a.check_in and a.check_out else 0
                )
                for a in help_atts
            )
            att_elks_miles = sum(elks_atts.mapped("x_miles") or [0.0])
            att_help_miles = sum(help_atts.mapped("x_miles") or [0.0])
            att_elks_count = len(set(elks_atts.mapped("employee_id.id")))
            att_help_count = len(set(help_atts.mapped("employee_id.id")))
            att_cash = sum(same_bucket.mapped("x_cash_value") or [0.0])
            att_non_cash = sum(
                same_bucket.mapped("x_non_cash_value") or [0.0]
            )

            # Existing contribution for this bucket?
            existing = Contrib.search([
                ("task_id", "=", task_id),
                ("contribution_date", "=", event_date),
                ("state", "not in", ("cancelled",)),
            ], order="id asc", limit=1)

            if existing:
                # Don't touch already-pushed rows.
                if existing.x_elks_org_state == "pushed":
                    continue
                # Determine what portion of existing.elks_hours came
                # from other-than-attendance sources (Quick Entry
                # declared - attendance actuals).  Simplest: set the
                # contribution's totals to attendance_totals + any
                # existing personal-record hours still on the
                # contribution.  Since _reconcile_pr_lines already
                # subtracted attendance-shadowed PR shares, the
                # residual elks_hours == PR hours for people WITHOUT
                # attendance.  Add attendance on top.
                current_elks = existing.elks_hours or 0.0
                current_help = existing.helper_hours or 0.0
                current_elks_miles = existing.elks_miles or 0.0
                current_help_miles = existing.helper_miles or 0.0
                # We only need to ADD attendance hours if they aren't
                # already reflected.  Detect via x_source:
                #   - x_source='attendance': this contribution was
                #     created by us — REPLACE totals with the fresh
                #     aggregate (handles the multi-clock-in-same-day
                #     case cleanly).
                #   - anything else (Quick Entry, manual, etc.):
                #     ADD attendance hours ONCE.  We track that via
                #     the recompute path: for attendance-source rows
                #     the aggregate is authoritative; for other
                #     sources, we recompute total = current + delta,
                #     where delta = attendance_totals - previously-
                #     absorbed attendance.
                #   Simplest correct behavior: use max() so we don't
                #   accidentally overwrite a Secretary's manual edit.
                if existing.x_source == "attendance":
                    existing.write({
                        "elks_hours": att_elks_hours,
                        "helper_hours": att_help_hours,
                        "elks_miles": att_elks_miles,
                        "helper_miles": att_help_miles,
                        "elks_count": att_elks_count,
                        "helper_count": att_help_count,
                        "cash_value": att_cash,
                        "non_cash_value": att_non_cash,
                        "x_elks_org_state": "not_pushed",
                        "x_elks_org_last_error": False,
                    })
                # Non-attendance sources (Quick Entry, manual): leave
                # the existing declared totals alone — _reconcile_pr
                # already adjusted them for shadowed attendance, and
                # the extension will push the whole thing to elks.org.
            else:
                # Fresh contribution — created straight to Confirmed
                # so it enters the extension push queue immediately.
                # Explicit x_elks_org_state so the row can't get stuck
                # in a limbo state via ORM default weirdness.
                Contrib.create({
                    "name": task.name,
                    "contribution_date": event_date,
                    "contribution_type": "service",
                    "task_id": task_id,
                    "elks_count": att_elks_count,
                    "helper_count": att_help_count,
                    "head_count": (
                        task.x_head_count
                        or (att_elks_count + att_help_count)
                    ),
                    "elks_hours": att_elks_hours,
                    "helper_hours": att_help_hours,
                    "elks_miles": att_elks_miles,
                    "helper_miles": att_help_miles,
                    "cash_value": att_cash,
                    "non_cash_value": att_non_cash,
                    "x_source": "attendance",
                    "x_elks_org_state": "not_pushed",
                    "state": "confirmed",
                    "submitted_by": self.env.user.id,
                    "confirmed_by": self.env.user.id,
                    "confirmed_date": fields.Datetime.now(),
                })

    @api.model
    def rebuild_attendance_contributions_button(self):
        """On-demand backfill.  Wraps _ensure_attendance_contribution
        for every validated charity attendance in the DB, then shows
        a notification with the before/after counts.  Handy when the
        version-based post-migrate didn't fire (Odoo already saw the
        target version), or after importing historical attendance
        from an outside system.
        """
        Contrib = self.env["elks.charity.contribution"].sudo()
        before = Contrib.search_count([("x_source", "=", "attendance")])

        historical = self.sudo().search([
            ("x_charity_task_id", "!=", False),
            ("x_validated", "=", True),
            ("check_out", "!=", False),
        ])
        if historical:
            historical._ensure_attendance_contribution()

        after = Contrib.search_count([("x_source", "=", "attendance")])
        delta = after - before
        _logger.info(
            "elkscharity: rebuild_attendance_contributions_button — "
            "scanned %d attendance row(s), %d attendance-sourced "
            "contribution(s) after (delta %+d).",
            len(historical), after, delta,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Rebuild Attendance Contributions"),
                "message": _(
                    "Scanned %(n)d validated charity attendance "
                    "row(s).  Attendance-sourced contributions: "
                    "%(before)d before → %(after)d after "
                    "(%(delta)+d new/updated).  Refresh 'Pending "
                    "Elks.org Push' to see them.",
                    n=len(historical), before=before,
                    after=after, delta=delta,
                ),
                "sticky": True,
                "type": "success" if delta >= 0 else "warning",
            },
        }

    def unlink(self):
        tasks = self.mapped('x_charity_task_id')
        res = super().unlink()
        if tasks:
            self._invalidate_charity_tasks(tasks)
        return res
