# -*- coding: utf-8 -*-
"""Self-install wizard for the Playwright + Chromium dependencies used
by the elks.org auto-push.

Opens a dialog in Configuration → Elks.org Push Setup with a big
"Run Installation" button.  Clicking it:

  1. Reports what's currently installed (playwright package? Chromium
     browser? system libraries?).
  2. Runs  <venv>/bin/pip install playwright  as a subprocess if the
     Python package is missing.
  3. Runs  <venv>/bin/playwright install chromium  to download the
     browser binary if it's missing.
  4. Attempts  <venv>/bin/playwright install-deps chromium  for
     system libraries — this needs root, so if the Odoo user can't
     sudo, we surface a clean copy-and-paste command for the admin
     to run once.

Everything is captured in the wizard's output field so the Secretary
sees exactly what happened without needing terminal access.
"""
import logging
import os
import shutil
import subprocess
import sys

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class ElksOrgSetupWizard(models.TransientModel):
    _name = "elks.charity.elks_org_setup_wizard"
    _description = "Elks.org Push — Server Setup"

    installation_log = fields.Text(
        "Installation Output", readonly=True,
    )
    playwright_installed = fields.Boolean(
        "Playwright Package", compute="_compute_status",
    )
    playwright_version = fields.Char(
        "Playwright Version", compute="_compute_status",
    )
    chromium_installed = fields.Boolean(
        "Chromium Browser", compute="_compute_status",
    )
    chromium_path = fields.Char(
        "Chromium Path", compute="_compute_status",
    )
    apt_deps_command = fields.Char(
        "System Deps Install Command", compute="_compute_status",
        help="Command an admin runs once (needs sudo) to install "
             "Chromium's Linux runtime libraries.",
    )

    # ------------------------------------------------------------------
    # Status probe
    # ------------------------------------------------------------------
    @api.depends()
    def _compute_status(self):
        for rec in self:
            # Playwright package
            try:
                import playwright
                rec.playwright_installed = True
                rec.playwright_version = getattr(
                    playwright, "__version__", "unknown"
                )
            except ImportError:
                rec.playwright_installed = False
                rec.playwright_version = False

            # Chromium binary
            path = self._chromium_binary_path()
            rec.chromium_installed = bool(path)
            rec.chromium_path = path or False

            # Suggested sudo command for system deps
            rec.apt_deps_command = (
                "sudo %s -m playwright install-deps chromium"
                % sys.executable
            )

    @staticmethod
    def _chromium_binary_path():
        """Return the path to Playwright's Chromium binary if present."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as p:
                exe = p.chromium.executable_path
                if exe and os.path.exists(exe):
                    return exe
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Install actions
    # ------------------------------------------------------------------
    def action_install_all(self):
        """Full install sequence: pip → playwright browser → note deps."""
        self.ensure_one()
        log = []
        log.append("=" * 70)
        log.append("Elks.org Push — server dependency install")
        log.append(f"Odoo user: {os.getlogin() if hasattr(os, 'getlogin') else '?'}")
        log.append(f"Python:    {sys.executable}")
        log.append(f"Venv:      {sys.prefix}")
        log.append("=" * 70)

        # Step 1: pip install playwright
        log.append("")
        log.append("STEP 1  ─  pip install playwright")
        log.append("-" * 70)
        ok, out = self._run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "playwright"],
            timeout=300,
        )
        log.append(out)
        if not ok:
            log.append("⚠️  pip install failed.  Try running manually:")
            log.append(f"    {sys.executable} -m pip install playwright")
            self.installation_log = "\n".join(log)
            return self._reopen_wizard()

        # Reload the playwright module in case it was just installed.
        # (No __import__ tricks needed — subsequent import will find it.)

        # Step 2: playwright install chromium
        log.append("")
        log.append("STEP 2  ─  playwright install chromium (~180 MB)")
        log.append("-" * 70)
        ok, out = self._run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            timeout=600,
        )
        log.append(out)
        if not ok:
            log.append("⚠️  Chromium download failed.  Retry manually:")
            log.append(f"    {sys.executable} -m playwright install chromium")

        # Step 3: system library deps.  This needs root, so we tell the
        # admin what to run rather than trying (and failing) ourselves.
        log.append("")
        log.append("STEP 3  ─  system libraries (needs sudo)")
        log.append("-" * 70)
        log.append(
            "System runtime libraries (libnss3, libatk1, libx11, etc.) "
            "typically require root to install.  Ask your admin to run "
            "this ONCE on the server:"
        )
        log.append("")
        log.append(f"    sudo {sys.executable} -m playwright install-deps chromium")
        log.append("")
        log.append(
            "Odoo doesn't run as root, so we can't do this from here.  "
            "If Chromium already runs on this box (e.g., wkhtmltopdf, "
            "pdf reports, etc.), the deps are probably already installed "
            "and you can skip this step."
        )

        # Step 4: verify by re-probing status
        log.append("")
        log.append("STEP 4  ─  Verification")
        log.append("-" * 70)
        self._compute_status()
        log.append(
            f"  playwright package : "
            f"{'YES ✅ ' + (self.playwright_version or '') if self.playwright_installed else 'NO ❌'}"
        )
        log.append(
            f"  Chromium browser   : "
            f"{'YES ✅ ' + (self.chromium_path or '') if self.chromium_installed else 'NO ❌'}"
        )
        log.append("")
        if self.playwright_installed and self.chromium_installed:
            log.append(
                "🎉  Setup complete.  If Chromium fails to launch when "
                "pushing, run the sudo command from Step 3 to add the "
                "missing system libs."
            )
        else:
            log.append(
                "⚠️  Some steps didn't complete.  Review the output above."
            )

        self.installation_log = "\n".join(log)
        return self._reopen_wizard()

    def action_check_status(self):
        """Refresh the status probe without installing anything."""
        self.ensure_one()
        self._compute_status()
        log = [
            "STATUS CHECK",
            "-" * 40,
            f"Python:           {sys.executable}",
            f"Venv:             {sys.prefix}",
            f"playwright pkg:   "
            f"{'YES ' + (self.playwright_version or '') if self.playwright_installed else 'NO'}",
            f"Chromium binary:  "
            f"{'YES ' + (self.chromium_path or '') if self.chromium_installed else 'NO'}",
            "",
            "For system libs (needs sudo):",
            f"    {self.apt_deps_command}",
        ]
        self.installation_log = "\n".join(log)
        return self._reopen_wizard()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _run(cmd, timeout=300):
        """Run a subprocess, capture output, return (ok, combined_output)."""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, (
                "Command timed out after %d seconds:\n    %s"
                % (timeout, " ".join(cmd))
            )
        except FileNotFoundError as e:
            return False, "Command not found: %s\n%s" % (" ".join(cmd), e)
        except Exception as e:
            return False, "Unexpected error: %s\n%s" % (" ".join(cmd), e)

        tail = 4000  # keep the wizard readable but useful
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        parts = []
        if out:
            parts.append("STDOUT:\n" + out[-tail:])
        if err:
            parts.append("STDERR:\n" + err[-tail:])
        parts.append("Exit code: %d" % result.returncode)
        return result.returncode == 0, "\n".join(parts)

    def _reopen_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
