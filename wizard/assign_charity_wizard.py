# -*- coding: utf-8 -*-
"""Mass-assign a charity activity to selected attendance records.

Usage:  From the Attendance list view, select one or more records →
Action → "Assign Charity Activity".  Pick the activity and optionally
override the charity hours, then hit Apply.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AssignCharityWizard(models.TransientModel):
    _name = "elks.assign.charity.wizard"
    _description = "Mass Assign Charity Activity to Attendance"

    charity_task_id = fields.Many2one(
        "project.task",
        string="Charity Activity",
        domain="[('x_is_charity_activity', '=', True)]",
        required=True,
        help="Select the charity activity to assign to the selected "
             "attendance records.",
    )
    use_worked_hours = fields.Boolean(
        "Set charity hours = worked hours",
        default=True,
        help="When checked, each record's charity hours will be set to "
             "its worked hours.  Uncheck to enter a fixed value instead.",
    )
    fixed_charity_hours = fields.Float(
        "Fixed Charity Hours",
        help="Set all selected records to this many charity hours.",
    )
    is_helper = fields.Boolean(
        "Non-Elk Helper", default=False,
        help="Check if these are non-Elk volunteer hours.",
    )
    attendance_count = fields.Integer(
        "Records Selected", compute="_compute_attendance_count",
    )

    @api.depends_context("active_ids")
    def _compute_attendance_count(self):
        for wiz in self:
            wiz.attendance_count = len(
                self.env.context.get("active_ids", [])
            )

    def action_apply(self):
        """Write the charity activity to all selected attendance records."""
        self.ensure_one()
        active_ids = self.env.context.get("active_ids", [])
        if not active_ids:
            raise UserError(_("No attendance records selected."))

        attendances = self.env["hr.attendance"].browse(active_ids)

        vals = {
            "x_charity_task_id": self.charity_task_id.id,
            "x_is_helper": self.is_helper,
        }

        if self.use_worked_hours:
            # Per-record: set charity hours = worked hours
            for att in attendances:
                att.write({
                    **vals,
                    "x_charity_hours": att.worked_hours or 0.0,
                })
        else:
            vals["x_charity_hours"] = self.fixed_charity_hours
            attendances.write(vals)

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Charity Activity Assigned"),
                "message": _("%d attendance record(s) tagged with '%s'.") % (
                    len(attendances), self.charity_task_id.name,
                ),
                "type": "success",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
