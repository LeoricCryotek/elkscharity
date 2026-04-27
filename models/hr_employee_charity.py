# -*- coding: utf-8 -*-
"""Extend hr.employee for charity kiosk integration and payroll timecards.

Adds a 'Default Charity Activity' field to the employee record.  When
set, any attendance check-in (including kiosk) will automatically tag
the resulting hr.attendance record with that charity activity so
volunteers don't have to manually edit each attendance after the fact.

The kiosk frontend also passes an explicit charity_task_id via context
when the user selects an activity from the kiosk dropdown; that takes
priority over the default.

Also adds a smart button on the employee form to open the Timecard
Report wizard pre-filled for that employee.
"""
from odoo import api, fields, models, _


class HrEmployeeCharity(models.Model):
    _inherit = "hr.employee"

    x_default_charity_task_id = fields.Many2one(
        "project.task", string="Default Charity Activity",
        domain="[('x_is_charity_activity', '=', True)]",
        help="When set, new attendance check-ins (including kiosk) will "
             "automatically be tagged with this charity activity.  Clear "
             "this field to stop auto-tagging future attendance.",
    )

    def _attendance_action_change(self, geo_information=None):
        """Override to auto-tag new attendance with charity activity.

        Priority:
          1. Explicit kiosk selection via context key 'kiosk_charity_task_id'
          2. Employee's default charity activity (x_default_charity_task_id)
          3. No tagging
        """
        was_checked_in = self.attendance_state == 'checked_in'
        attendance = super()._attendance_action_change(geo_information=geo_information)

        # Only tag on CHECK-IN (not check-out)
        if not was_checked_in and attendance:
            # Priority 1: explicit selection from kiosk
            charity_task_id = self.env.context.get('kiosk_charity_task_id')
            # Priority 2: default on employee record
            if not charity_task_id and self.x_default_charity_task_id:
                charity_task_id = self.x_default_charity_task_id.id

            if charity_task_id:
                # Validate the task exists and is a charity activity
                task = self.env['project.task'].sudo().browse(charity_task_id).exists()
                if task:
                    vals = {'x_charity_task_id': task.id}
                    # Also default the charity hours to worked_hours
                    if attendance.worked_hours:
                        vals['x_charity_hours'] = attendance.worked_hours
                    attendance.write(vals)

        return attendance

    def action_open_timecard(self):
        """Open the Timecard Report wizard pre-filled for this employee."""
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'elkscharity.action_elks_timecard_report_wizard'
        )
        action['context'] = {
            'default_employee_ids': [self.id],
        }
        return action
