# -*- coding: utf-8 -*-
# =============================================================================
# === HUMAN ===
# The "Volunteer Hours Leaderboard" brain. It ranks Elk members by the charity
# hours they've logged and is the SINGLE place that math lives, so the printed
# bulletin block and the public website leaderboard always show the same numbers.
# Two views: this MONTH and this LODGE YEAR (Apr–Mar). It counts every hour a
# member submitted (validated or not) and leaves OUT non-Elk helper hours — this
# is a board for Elks. Also holds the shared "why we volunteer" note both places
# print.
#
# === AI AGENT ===
# AbstractModel (no table) exposing get_leaderboard()/get_dual(). Data source is
# elks.charity.hours.report — the unified timesheet+attendance SQL view — grouped
# by employee via _read_group(aggregates=["hours:sum"]). Elks-only = is_helper
# False; "all submitted" = no validated filter. employee_id is mapped to the
# member res.partner through elkscontacts' x_volunteer_employee_id (reverse
# search) for the display name / profile link, falling back to the employee name.
# Lodge year is the standard Elks Apr–Mar window (see _lodge_year_str), matching
# elks.charity.dashboard._current_lodge_year and the bulletin's officer logic.
# Called with sudo() from the public website controller (aggregated-but-named,
# per the lodge's explicit choice to show full names publicly).
# =============================================================================
import calendar

from odoo import api, fields, models


# Shared "duty of an Elk" blurb printed under both boards. Kept here so the
# bulletin and the website never drift apart.
VOLUNTEER_NOTE = (
    "Charity and service are the heart of who we are. Every hour an Elk "
    "gives — at a fundraiser, a youth event, a veterans project — "
    "lifts our community and lives out our pledge that Elks Care and Elks "
    "Share. Thank you to everyone who logged time; you set the example we all "
    "strive to follow. If you volunteered, be sure your hours are recorded so "
    "your lodge gets credit — and so you land on next month's board."
)


def _lodge_year_str(d):
    """'YYYY-YYYY' Elks lodge year (Apr 1 -> Mar 31) that contains date d."""
    return f"{d.year}-{d.year + 1}" if d.month >= 4 else f"{d.year - 1}-{d.year}"


class ElksCharityLeaderboard(models.AbstractModel):
    _name = "elks.charity.leaderboard"
    _description = "Volunteer Hours Leaderboard (shared logic)"

    # Exposed so callers can print/return the note without importing the module.
    @api.model
    def volunteer_note(self):
        return VOLUNTEER_NOTE

    @api.model
    def _coerce_date(self, ref_date):
        if not ref_date:
            return fields.Date.context_today(self)
        if isinstance(ref_date, str):
            return fields.Date.to_date(ref_date)
        return ref_date

    @api.model
    def _format_name(self, name, name_mode):
        """'full' -> "Danny Santiago"; 'initial' -> "Danny S."."""
        name = (name or "").strip()
        if name_mode == "initial" and name:
            parts = name.split()
            if len(parts) > 1:
                return "%s %s." % (parts[0], parts[-1][:1])
        return name

    @api.model
    def range_label(self, start, end):
        """A human label for a date window, e.g. 'Jun 1 – Aug 10, 2026'."""
        s, e = self._coerce_date(start), self._coerce_date(end)
        left = ("%s %d" % (s.strftime("%b"), s.day)
                if s.year == e.year else "%s %d, %d" % (s.strftime("%b"), s.day, s.year))
        return "%s – %s %d, %d" % (left, e.strftime("%b"), e.day, e.year)

    @api.model
    def get_leaderboard(self, period="month", ref_date=None, limit=10,
                        name_mode="full", start=None, end=None):
        """Ranked volunteer hours by Elk member.

        period    : 'month' (calendar month of ref_date) or 'lodge_year'
                    (Elks fiscal year Apr–Mar containing ref_date).
        ref_date  : date or 'YYYY-MM-DD' string; defaults to today.
        limit     : max rows (10 = 1st..10th place).
        name_mode : 'full' or 'initial'.
        start/end : if either is given, rank over that explicit date window
                    (inclusive) instead of period/ref_date — for a custom range.
        Returns an ordered list of dicts:
            {rank, name, hours, employee_id, partner_id, period_label}
        """
        domain = [("is_helper", "=", False)]  # Elks only, not helper hours
        if start or end:
            s = self._coerce_date(start) if start else fields.Date.to_date("1900-01-01")
            e = self._coerce_date(end) if end else fields.Date.to_date("2999-12-31")
            domain += [("date", ">=", s), ("date", "<=", e)]
            period_label = self.range_label(s, e)
        elif period == "lodge_year":
            ref_date = self._coerce_date(ref_date)
            period_label = _lodge_year_str(ref_date)
            domain.append(("lodge_year", "=", period_label))
        else:
            ref_date = self._coerce_date(ref_date)
            first = ref_date.replace(day=1)
            last = ref_date.replace(
                day=calendar.monthrange(ref_date.year, ref_date.month)[1])
            domain += [("date", ">=", first), ("date", "<=", last)]
            period_label = ref_date.strftime("%B %Y")

        Report = self.env["elks.charity.hours.report"]
        groups = Report._read_group(
            domain, groupby=["employee_id"], aggregates=["hours:sum"])
        rows = [(emp, total) for emp, total in groups if emp and (total or 0) > 0]
        # Highest hours first; stable tiebreak on name so ties are deterministic.
        rows.sort(key=lambda r: (-r[1], (r[0].name or "").lower()))
        rows = rows[:max(limit, 0)]

        # employee -> member partner (display name + profile link).
        emp_ids = [emp.id for emp, _ in rows]
        partners = (self.env["res.partner"].sudo().search(
            [("x_volunteer_employee_id", "in", emp_ids)])
            if emp_ids else self.env["res.partner"])
        emp2partner = {
            p.x_volunteer_employee_id.id: p
            for p in partners if p.x_volunteer_employee_id
        }

        result = []
        for i, (emp, total) in enumerate(rows, start=1):
            partner = emp2partner.get(emp.id)
            name = (partner.name if partner else emp.name) or "Unknown Elk"
            result.append({
                "rank": i,
                "name": self._format_name(name, name_mode),
                "hours": round(total, 1),
                "employee_id": emp.id,
                "partner_id": partner.id if partner else False,
                "period_label": period_label,
            })
        return result

    @api.model
    def get_dual(self, ref_date=None, limit=10, name_mode="full"):
        """Both boards at once (used by both front-ends)."""
        rd = self._coerce_date(ref_date)
        return {
            "month": self.get_leaderboard("month", rd, limit, name_mode),
            "lodge_year": self.get_leaderboard("lodge_year", rd, limit, name_mode),
            "month_label": rd.strftime("%B %Y"),
            "year_label": _lodge_year_str(rd),
            "note": VOLUNTEER_NOTE,
        }
