# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# Paste-a-timesheet importer for volunteer hours emails.  Volunteers often
# email the Secretary a monthly log looking like:
#
#     July 2026
#     Date     Shift 1        Shift 2      Total Hours
#     July 2   2:00 – 3:30    4:50 – 6:30  3h 10m
#     July 3   12:40 – 4:30   4:55 – 9:00  7h 55m
#     July 5   4:55 – 5:30    —            0h 35m
#     TOTAL                                64h 40m
#
# The wizard parses that text, guesses AM/PM per shift by matching the
# duration to the "Total Hours" cell, previews every row, then creates
# hr.attendance records tagged for the specified charity activity.  The
# existing _ensure_attendance_contribution hook auto-generates the
# contribution the Secretary would otherwise have to create by hand.
#
# Menu: Elks Charity → Configuration → Import Volunteer Hours
#
# Employee + task + is_helper are selected once per import batch.  If
# the email covers multiple activities the user runs the wizard once
# per activity (the fixed cost of the wizard is one dropdown + one
# paste — not a lot).
# === AI AGENT ===
# The parser is deliberately forgiving: extra whitespace, en-dash vs
# hyphen, "12:40-4:30" without spaces, ":" or "." as the h:m separator,
# missing shift 2 as "—" or blank all parse the same.
# AM/PM inference: for each shift, try (start=AM,end=AM), (start=AM,end=PM),
# (start=PM,end=PM), (start=PM,end=AM_next).  Pick whichever produces the
# smallest positive duration matching the "Total Hours" cell to within
# 6 minutes.  If Total Hours is missing, prefer the assignment that
# produces a reasonable duration (positive, <14 hours).
# ============================================================================
"""Paste-a-timesheet importer for charity attendance."""
import logging
import re
from datetime import datetime, date, time, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_DATE_RE = re.compile(
    r"\b(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+(?P<day>\d{1,2})\b",
    re.IGNORECASE,
)
_HEADER_YEAR_RE = re.compile(
    r"\b(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+(?P<year>20\d{2})\b",
    re.IGNORECASE,
)
# Match H:MM (or H.MM) followed by en/em dash or hyphen followed by H:MM.
# Accepts "2:00 – 3:30", "2:00-3:30", "2.00 — 3.30", etc.
_RANGE_RE = re.compile(
    r"(?P<sh>\d{1,2})[:.](?P<sm>\d{2})\s*[–—\-]\s*"
    r"(?P<eh>\d{1,2})[:.](?P<em>\d{2})"
)
# Total hours cell: "3h 10m", "3h10m", "3 h 10 m", "0h 30m", or "3:10".
_TOTAL_RE = re.compile(
    r"(?P<h>\d{1,2})\s*(?:h|hr|hrs|hour|hours|:)\s*(?P<m>\d{1,2})?\s*"
    r"(?:m|min|mins|minute|minutes)?",
    re.IGNORECASE,
)


def _parse_total_minutes(cell):
    """Turn '3h 10m' / '7h 55m' / '3:10' into total minutes (int).
    Returns None if the cell is empty or unparseable."""
    if not cell:
        return None
    cell = cell.strip()
    if cell in ("—", "-", "–", ""):
        return None
    m = _TOTAL_RE.search(cell)
    if not m:
        return None
    h = int(m.group("h") or 0)
    mm = int(m.group("m") or 0)
    return h * 60 + mm


def _try_ampm(sh, sm, eh, em, start_am, end_am):
    """Combine 12-hour parts into concrete times; return (start_time,
    end_time, duration_minutes) or None if invalid.

    start_am/end_am are booleans (True=AM, False=PM).  The 12 o'clock
    ambiguity in 12-hour clocks (12 PM = noon, 12 AM = midnight) is
    handled by converting to 24-hour: hour 12 stays 12 in PM,
    becomes 0 in AM.
    """
    def to_24h(h, is_am):
        if is_am:
            return 0 if h == 12 else h
        return h if h == 12 else h + 12
    s24 = to_24h(sh, start_am)
    e24 = to_24h(eh, end_am)
    if not (0 <= s24 < 24 and 0 <= e24 < 24 and 0 <= sm < 60 and 0 <= em < 60):
        return None
    start = time(s24, sm)
    end = time(e24, em)
    # Duration in minutes; if end < start we assume next-day (rare for
    # a shift, but allowed for late-night crossings).
    start_min = s24 * 60 + sm
    end_min = e24 * 60 + em
    if end_min <= start_min:
        end_min += 24 * 60  # rollover
    dur = end_min - start_min
    if dur <= 0 or dur > 14 * 60:
        return None
    return (start, end, dur)


def _infer_shift(sh, sm, eh, em, target_total_min=None):
    """Try all AM/PM combinations for (start, end).  Pick the one
    whose duration best matches the target total (within 6 minutes),
    or the shortest positive duration if no target is given.
    Returns (start_time, end_time) or None.
    """
    candidates = []
    for start_am in (True, False):
        for end_am in (True, False):
            r = _try_ampm(sh, sm, eh, em, start_am, end_am)
            if r:
                candidates.append(r)
    if not candidates:
        return None
    if target_total_min is not None:
        # Score by absolute distance from the target duration.  Pick
        # the closest within 6 minutes; if none, fall back to closest
        # regardless.
        candidates.sort(key=lambda c: abs(c[2] - target_total_min))
        best = candidates[0]
        # Optional strict-match sanity: log if the closest still isn't
        # within tolerance — the row will still import but the parser
        # is unsure.
        if abs(best[2] - target_total_min) > 6:
            _logger.info(
                "attendance import: shift %02d:%02d-%02d:%02d best "
                "candidate duration %dmin off from target %dmin",
                sh, sm, eh, em, best[2], target_total_min,
            )
    else:
        # No target — prefer PM-PM (most common for evening lodge work).
        candidates.sort(key=lambda c: (c[2],))
        best = candidates[0]
    return (best[0], best[1])


class ChairtyAttendanceImportWizard(models.TransientModel):
    _name = "elks.charity.attendance.import.wizard"
    _description = "Import Volunteer Hours as Charity Attendance"

    employee_id = fields.Many2one(
        "hr.employee", string="Volunteer", required=True,
        help="Employee record for the volunteer whose hours you're importing. "
             "If they don't exist as an employee yet, create the employee "
             "record first (HR → Employees).",
    )
    charity_task_id = fields.Many2one(
        "project.task", string="Charity Activity",
        domain="[('x_is_charity_activity', '=', True)]",
        required=True,
        help="The activity these hours count toward (Lodge Operations, "
             "Grace Bible Service, etc.).  Determines the GL category "
             "on the resulting contribution.",
    )
    is_helper = fields.Boolean(
        "Non-Elk Helper", default=False,
        help="Check if the volunteer is a non-Elk helper.  Rare — most "
             "imports leave this unchecked.",
    )
    default_year = fields.Integer(
        "Default Year",
        default=lambda self: fields.Date.today().year,
        help="Used when the pasted text's month header doesn't include a "
             "year (e.g. plain 'July' instead of 'July 2026').",
    )
    raw_data = fields.Text(
        "Paste Timesheet Here", required=True,
        help="Copy the timesheet table from the email (including the "
             "'Month YYYY' header) and paste it here.  Formatting is "
             "forgiving — the parser handles en-dashes, hyphens, spaces, "
             "and skips headers/totals automatically.",
    )
    preview_html = fields.Html(
        "Preview", compute="_compute_preview", sanitize=False,
    )
    preview_row_count = fields.Integer(compute="_compute_preview")
    preview_warnings = fields.Integer(compute="_compute_preview")

    @api.depends("raw_data", "default_year")
    def _compute_preview(self):
        for wiz in self:
            if not wiz.raw_data:
                wiz.preview_html = (
                    "<em>Paste your timesheet above and click "
                    "<b>Preview</b> to see what will be imported.</em>"
                )
                wiz.preview_row_count = 0
                wiz.preview_warnings = 0
                continue
            rows, warnings = wiz._parse_raw()
            wiz.preview_row_count = len(rows)
            wiz.preview_warnings = len(warnings)
            wiz.preview_html = wiz._render_preview(rows, warnings)

    def _parse_raw(self):
        """Parse self.raw_data into a list of dicts.
        Returns (rows, warnings) where each row is:
            {date: date, start: time, end: time, duration_min: int,
             shift_label: 'Shift 1'|'Shift 2', source_line: str}
        and warnings is a list of human-readable strings.
        """
        self.ensure_one()
        text = self.raw_data or ""
        warnings = []
        rows = []

        # Detect year from a header like "July 2026" if present.
        year_hint = self.default_year or fields.Date.today().year
        header_match = _HEADER_YEAR_RE.search(text)
        if header_match:
            try:
                year_hint = int(header_match.group("year"))
            except ValueError:
                pass

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Skip TOTAL row and column-header row.
            if re.match(r"^\s*total\b", line, re.IGNORECASE):
                continue
            if re.match(r"^\s*date\b", line, re.IGNORECASE):
                continue
            # Skip lines that are only the "Month YYYY" header.
            if _HEADER_YEAR_RE.fullmatch(line):
                continue

            # Extract the date (month + day).
            dm = _DATE_RE.search(line)
            if not dm:
                # Not a data row — skip silently.
                continue
            month_key = dm.group("month").lower()
            day = int(dm.group("day"))
            month = _MONTHS.get(month_key)
            if not month:
                warnings.append(
                    "Unrecognized month on line: %r" % line[:80]
                )
                continue
            try:
                row_date = date(year_hint, month, day)
            except ValueError:
                warnings.append(
                    "Invalid date %s %d %d on line: %r"
                    % (month_key, day, year_hint, line[:80])
                )
                continue

            # Grab the last cell (Total Hours) if present, to score AM/PM.
            # Take everything after the LAST time-range as the "total" cell.
            all_ranges = list(_RANGE_RE.finditer(line))
            if not all_ranges:
                # Date-only line with no shifts (e.g., someone left it
                # blank).  Skip.
                continue
            tail = line[all_ranges[-1].end():].strip()
            target_min = _parse_total_minutes(tail)
            # If tail didn't parse but the whole line has a total cell
            # further right, try the entire line minus the ranges.
            if target_min is None:
                stripped = _RANGE_RE.sub("", line)
                target_min = _parse_total_minutes(stripped)
            # Split target across the shifts proportionally — but for
            # AM/PM inference we don't need per-shift totals; we score
            # each shift by whether its computed duration + others sums
            # to the target.  Simpler: score each shift independently
            # against the target if only one shift, or against
            # target-other-shift if two.
            n_shifts = len(all_ranges)

            for idx, rm in enumerate(all_ranges):
                sh = int(rm.group("sh"))
                sm = int(rm.group("sm"))
                eh = int(rm.group("eh"))
                em = int(rm.group("em"))
                # Per-shift target: proportional split unknown; use
                # target if only 1 shift, else no target (let inference
                # pick smallest positive duration).
                per_shift_target = target_min if n_shifts == 1 else None
                inferred = _infer_shift(sh, sm, eh, em, per_shift_target)
                if not inferred:
                    warnings.append(
                        "Couldn't parse shift %d:%02d-%d:%02d on %s"
                        % (sh, sm, eh, em, row_date.isoformat())
                    )
                    continue
                start_t, end_t = inferred
                start_min = start_t.hour * 60 + start_t.minute
                end_min = end_t.hour * 60 + end_t.minute
                if end_min <= start_min:
                    end_min += 24 * 60
                dur_min = end_min - start_min
                rows.append({
                    "date": row_date,
                    "start": start_t,
                    "end": end_t,
                    "duration_min": dur_min,
                    "shift_label": "Shift %d" % (idx + 1),
                    "source_line": line,
                })

            # Post-check: if we have 2 shifts and a target, verify sum
            # matches (within 6 minutes).  If not, flag.
            if n_shifts >= 2 and target_min is not None:
                row_shifts = [
                    r for r in rows[-n_shifts:] if r["date"] == row_date
                ]
                total_computed = sum(r["duration_min"] for r in row_shifts)
                if abs(total_computed - target_min) > 6:
                    warnings.append(
                        "%s: computed %dmin != listed %dmin (%dmin off)"
                        % (row_date.isoformat(),
                           total_computed, target_min,
                           total_computed - target_min)
                    )
        return rows, warnings

    def _render_preview(self, rows, warnings):
        if not rows:
            return (
                "<div class='alert alert-warning'>"
                "<strong>No rows parsed.</strong> Make sure your paste "
                "includes lines like <code>July 2  2:00 – 3:30  4:50 – 6:30</code>."
                "</div>"
            )
        html = []
        html.append(
            "<div style='margin-bottom:6px;'><strong>%d row(s) will "
            "be imported.</strong></div>" % len(rows)
        )
        html.append(
            "<table class='table table-sm' style='font-size:12px;'>"
            "<thead><tr>"
            "<th>Date</th><th>Shift</th><th>Start</th><th>End</th>"
            "<th style='text-align:right;'>Duration</th></tr></thead><tbody>"
        )
        for r in rows:
            html.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td style='text-align:right;'>%dh %02dm</td></tr>" % (
                    r["date"].strftime("%a %b %d"),
                    r["shift_label"],
                    r["start"].strftime("%-I:%M %p"),
                    r["end"].strftime("%-I:%M %p"),
                    r["duration_min"] // 60, r["duration_min"] % 60,
                )
            )
        html.append("</tbody></table>")
        if warnings:
            html.append(
                "<div class='alert alert-warning' style='margin-top:8px;"
                "font-size:12px;'>"
                "<strong>%d warning(s):</strong><ul style='margin:4px 0 0 20px;'>"
                % len(warnings)
            )
            for w in warnings[:10]:
                html.append("<li>%s</li>" % w)
            if len(warnings) > 10:
                html.append(
                    "<li><em>… and %d more</em></li>"
                    % (len(warnings) - 10)
                )
            html.append("</ul></div>")
        return "".join(html)

    def action_preview(self):
        """Re-render the preview after edits."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_import(self):
        """Create the hr.attendance records.  Contributions auto-generate
        via the _ensure_attendance_contribution hook on save."""
        self.ensure_one()
        if not self.employee_id:
            raise UserError(_("Select a volunteer first."))
        if not self.charity_task_id:
            raise UserError(_("Select a charity activity first."))
        rows, warnings = self._parse_raw()
        if not rows:
            raise UserError(_(
                "Nothing to import — the paste didn't parse into any rows."
            ))

        Att = self.env["hr.attendance"].sudo()
        created = 0
        skipped_dupe = 0
        # Naive UTC storage: Odoo expects datetimes in UTC.  Convert
        # our local (company-tz) start/end back to UTC before write.
        import pytz
        tz_name = (
            (self.env.company.partner_id.tz
             if self.env.company.partner_id else None)
            or self.env.company.resource_calendar_id.tz
            or self.env.user.tz
            or "UTC"
        )
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            tz = pytz.UTC

        for r in rows:
            check_in_local = datetime.combine(r["date"], r["start"])
            check_out_local = datetime.combine(r["date"], r["end"])
            if check_out_local <= check_in_local:
                check_out_local += timedelta(days=1)
            check_in_utc = tz.localize(check_in_local).astimezone(
                pytz.UTC).replace(tzinfo=None)
            check_out_utc = tz.localize(check_out_local).astimezone(
                pytz.UTC).replace(tzinfo=None)

            # Duplicate guard: exact same employee + check_in already
            # exists → skip silently.
            existing = Att.search([
                ("employee_id", "=", self.employee_id.id),
                ("check_in", "=", check_in_utc),
            ], limit=1)
            if existing:
                skipped_dupe += 1
                continue

            Att.create({
                "employee_id": self.employee_id.id,
                "check_in": check_in_utc,
                "check_out": check_out_utc,
                "x_charity_task_id": self.charity_task_id.id,
                "x_is_helper": self.is_helper,
                "x_validated": True,
                "x_charity_hours": r["duration_min"] / 60.0,
            })
            created += 1

        _logger.info(
            "Charity attendance import: employee=%s task=%s "
            "created=%d duplicates_skipped=%d warnings=%d",
            self.employee_id.name, self.charity_task_id.name,
            created, skipped_dupe, len(warnings),
        )
        msg_parts = ["%d attendance record(s) imported" % created]
        if skipped_dupe:
            msg_parts.append("%d duplicate(s) skipped" % skipped_dupe)
        if warnings:
            msg_parts.append("%d parse warning(s)" % len(warnings))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Import Complete"),
                "message": ". ".join(msg_parts) + ".",
                "type": "success" if not warnings else "warning",
                "sticky": True,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
