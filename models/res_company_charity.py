# -*- coding: utf-8 -*-
# ============================================================================
# === HUMAN ===
# 19.0.7.24: Company-level default charity task.  Used by the kiosk
# clock-in hook: when a volunteer clocks in via the Attendance kiosk
# and doesn't specify a Charity Activity, the attendance row gets
# tagged with this default so their hours flow to the right GL
# category automatically.  Lewiston Lodge sets this to "Lodge
# Operations" (GL 9999) — the catch-all for general volunteer time
# that isn't tied to a specific program.
# ============================================================================
"""Company settings — default charity task for kiosk attendance."""
from odoo import fields, models


class ResCompanyCharity(models.Model):
    _inherit = "res.company"

    x_default_charity_task_id = fields.Many2one(
        "project.task",
        string="Default Charity Activity (Kiosk)",
        domain="[('x_is_charity_activity', '=', True)]",
        help="Charity Activity applied automatically to attendance rows "
             "created via the Kiosk clock-in when the employee doesn't "
             "pick a specific activity.  Typically set to 'Lodge "
             "Operations' (GL 9999) so general volunteer hours count "
             "toward the lodge's charity report without every volunteer "
             "having to remember to tag their shift.",
    )


class ResConfigSettingsCharityDefault(models.TransientModel):
    _inherit = "res.config.settings"

    x_default_charity_task_id = fields.Many2one(
        "project.task",
        string="Default Charity Activity (Kiosk)",
        related="company_id.x_default_charity_task_id",
        readonly=False,
        domain="[('x_is_charity_activity', '=', True)]",
        help="Company default — applied to kiosk clock-ins that don't "
             "carry an explicit Charity Activity.",
    )

