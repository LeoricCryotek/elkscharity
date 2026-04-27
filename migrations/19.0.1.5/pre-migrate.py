# -*- coding: utf-8 -*-
"""Drop x_people_helped column from hr_attendance.

The field was moved to project.task (x_head_count) because "Head Count
of Participants" is per event, not per individual attendance record.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'hr_attendance'
          AND column_name = 'x_people_helped'
    """)
    if cr.fetchone():
        _logger.info("Dropping x_people_helped from hr_attendance (moved to project.task x_head_count)")
        cr.execute("ALTER TABLE hr_attendance DROP COLUMN x_people_helped")
