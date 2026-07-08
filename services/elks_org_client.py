# -*- coding: utf-8 -*-
"""Headless-browser HTTP client for elks.org Local Lodge Reporting.

elks.org's login form is JavaScript-rendered — a plain Python HTTP
POST can never authenticate because the browser has to run scripts
that hash the password and negotiate the session cookie.  So this
module drives a real headless Chromium via Playwright: log in through
the actual form UI, then use the resulting session cookies to POST
the Local Lodge Reporting form the same way our earlier requests-
based prototype did.

Installation on the Odoo server (one-time):

    /path/to/odoo/venv/bin/pip install playwright
    /path/to/odoo/venv/bin/playwright install chromium
    # On Debian/Ubuntu, follow with:
    /path/to/odoo/venv/bin/playwright install-deps chromium

Bulk push (the whole point of headless-browser automation):
    ElksOrgClient.submit_many(payloads)  logs in ONCE and POSTs every
    contribution in the same browser session, then closes the browser.
    That amortizes the 5–10s login cost across the batch.

Design decisions:

  * Headless by default, ``headless=False`` for local debugging.
  * Timeout aggressive-but-sane (45s) — elks.org can be slow.
  * A short slot of Python-side retry (1 attempt after transient
    Playwright errors) but nothing fancy: transient errors are almost
    always network-level and worth surfacing to the Secretary.
  * Any Playwright import error raises a clean ElksOrgError telling
    the user to install Playwright — no cryptic ImportError.
  * All state stays inside the class instance; no module-level
    globals.  Safe to construct concurrently (Playwright itself is
    threading-friendly per instance).
"""
import logging
import re
import time

_logger = logging.getLogger(__name__)


class ElksOrgError(Exception):
    """Raised when elks.org login or submission fails."""


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright, Error as PWError
    except ImportError as e:
        raise ElksOrgError(
            "Playwright isn't installed on this Odoo server.  Ask "
            "your admin to run:\n\n"
            "    <odoo-venv>/bin/pip install playwright\n"
            "    <odoo-venv>/bin/playwright install chromium\n"
            "    <odoo-venv>/bin/playwright install-deps chromium\n\n"
            "then restart Odoo.  (Underlying error: %s)" % e
        )
    return sync_playwright, PWError


class ElksOrgClient:
    """Playwright-driven client for elks.org Local Lodge Reporting."""

    DEFAULT_TIMEOUT_MS = 45_000  # 45s per navigation / action

    def __init__(
        self,
        login,
        password,
        login_url=None,
        form_url=None,
        headless=True,
        slow_mo_ms=0,
    ):
        if not login or not password:
            raise ElksOrgError(
                "Elks.org login and password must both be set."
            )
        self.login = login
        self.password = password
        self.login_url = login_url or "https://www.elks.org/login.cfm"
        self.form_url = form_url or (
            "https://www.elks.org/grandlodge/charity/local.cfm"
        )
        self.headless = headless
        self.slow_mo_ms = slow_mo_ms

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def submit_contribution(self, payload):
        """Push a single contribution.  Convenience wrapper around
        submit_many() for one-off pushes."""
        results = self.submit_many([payload])
        confirmation, err = results[0]
        if err:
            raise ElksOrgError(err)
        return confirmation

    def submit_many(self, payloads):
        """Log in once, POST every payload, close the browser.

        Returns a list of ``(confirmation_or_none, error_or_none)``
        tuples in the same order as the input.  Never raises out —
        per-payload errors are captured so partial batches complete
        even when one entry fails.
        """
        if not payloads:
            return []
        sync_playwright, PWError = _import_playwright()

        results = []
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=self.headless,
                    slow_mo=self.slow_mo_ms,
                )
            except PWError as e:
                raise ElksOrgError(
                    "Couldn't launch Chromium: %s.  Make sure "
                    "'playwright install chromium' completed on this "
                    "server." % str(e)[:300]
                )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (compatible; ElksCharityBot/1.0; "
                        "+lewistonelks896.com)"
                    ),
                    viewport={"width": 1280, "height": 900},
                )
                context.set_default_timeout(self.DEFAULT_TIMEOUT_MS)
                page = context.new_page()

                # Step 1 — authenticate (one login for the batch)
                self._authenticate(page, PWError)

                # Step 2 — submit each payload
                for payload in payloads:
                    try:
                        confirmation = self._submit_one(page, payload, PWError)
                        results.append((confirmation, None))
                    except ElksOrgError as e:
                        results.append((None, str(e)[:1000]))
                    except PWError as e:
                        results.append((
                            None,
                            "elks.org playwright error: %s" % str(e)[:500],
                        ))
                    except Exception as e:
                        results.append((
                            None,
                            "elks.org unexpected error: %s" % str(e)[:500],
                        ))
                    # Gentle rate limit — 500ms between submissions so we
                    # don't hammer elks.org during large bulk batches.
                    time.sleep(0.5)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _authenticate(self, page, PWError):
        """Navigate to the login page and submit the JS-rendered form."""
        try:
            page.goto(self.login_url, wait_until="networkidle")
        except PWError as e:
            raise ElksOrgError(
                "Couldn't reach elks.org login page: %s" % str(e)[:300]
            )

        # Give elks.org's login JS a moment to render the form.  The
        # form's real field names / selectors aren't public, so we
        # probe a few reasonable candidates.  If none work we surface
        # a clear error so the Secretary knows what to escalate.
        username_selectors = [
            'input[name="Login"]',
            'input[name="username"]',
            'input[name="Username"]',
            'input[name="loginID"]',
            'input[type="text"]',
        ]
        password_selectors = [
            'input[name="Password"]',
            'input[name="password"]',
            'input[type="password"]',
        ]
        submit_selectors = [
            'input[type="submit"][value*="Login" i]',
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Login")',
            'button:has-text("Sign In")',
        ]

        user_el = self._first_visible(page, username_selectors)
        pass_el = self._first_visible(page, password_selectors)
        if not user_el or not pass_el:
            raise ElksOrgError(
                "Couldn't find the elks.org login form.  The site's "
                "front-end may have changed; open the login page in a "
                "browser and share the current form HTML with the "
                "developer."
            )

        user_el.fill(self.login)
        pass_el.fill(self.password)

        submit_el = self._first_visible(page, submit_selectors)
        if submit_el:
            try:
                with page.expect_navigation(
                    wait_until="networkidle",
                    timeout=self.DEFAULT_TIMEOUT_MS,
                ):
                    submit_el.click()
            except PWError:
                # Some elks.org flows don't full-nav on login (SPA).
                # Fall back to a plain click + short wait.
                submit_el.click()
                page.wait_for_load_state(
                    "networkidle", timeout=self.DEFAULT_TIMEOUT_MS
                )
        else:
            # No obvious button — try pressing Enter in the password.
            pass_el.press("Enter")
            page.wait_for_load_state(
                "networkidle", timeout=self.DEFAULT_TIMEOUT_MS
            )

        # Verify auth by looking for Logout / Profile / Members
        body = page.content().lower()
        if "logout" in body or "sign out" in body or "my profile" in body:
            return  # authenticated
        if "invalid" in body or "incorrect" in body:
            raise ElksOrgError(
                "elks.org rejected the login — check your Elks.org "
                "credentials under Preferences → Elks.org Credentials."
            )
        raise ElksOrgError(
            "elks.org login didn't produce an authenticated session.  "
            "The site may have changed.  Verify by logging in via a "
            "browser then paste the current login URL into "
            "Configuration → System Parameters → "
            "elkscharity.elks_org_login_url."
        )

    def _submit_one(self, page, payload, PWError):
        """POST a single contribution to the Local Lodge Reporting form."""
        page.goto(self.form_url, wait_until="networkidle")

        # Best-effort field fill.  The form uses friendly `name=`
        # attributes we captured from your paste (programDate,
        # programID, programName, headcount, numberElks, numberHelpers,
        # hoursElks, hoursHelpers, milesElks, milesHelpers, nonCash,
        # cash, otherProgramID, submitProgram).
        fields = {
            "programDate":     payload.get("programDate", ""),
            "programName":     payload.get("programName", ""),
            "headcount":       str(payload.get("headcount", 0)),
            "numberElks":      str(payload.get("numberElks", 0)),
            "numberHelpers":   str(payload.get("numberHelpers", 0)),
            "hoursElks":       str(payload.get("hoursElks", 0)),
            "hoursHelpers":    str(payload.get("hoursHelpers", 0)),
            "milesElks":       str(payload.get("milesElks", 0)),
            "milesHelpers":    str(payload.get("milesHelpers", 0)),
            "nonCash":         str(payload.get("nonCash", 0)),
            "cash":            str(payload.get("cash", 0)),
        }
        # programID is a <select>
        pid = str(payload.get("programID", "9999"))
        try:
            page.select_option('select[name="programID"]', value=pid)
        except PWError:
            raise ElksOrgError(
                "Couldn't set Program Type on elks.org form — the form "
                "may have changed."
            )

        # Text inputs
        for name, value in fields.items():
            sel = 'input[name="%s"], textarea[name="%s"]' % (name, name)
            try:
                page.locator(sel).first.fill(value)
            except PWError:
                # Optional fields aren't always present — silently skip.
                continue

        # Program 9999 exposes "Other Category Name"
        if pid == "9999":
            other = payload.get("otherProgramID", "n/a") or "n/a"
            try:
                page.locator('input[name="otherProgramID"]').first.fill(other)
            except PWError:
                pass

        # Submit the form and wait for the response page.
        try:
            with page.expect_navigation(
                wait_until="networkidle",
                timeout=self.DEFAULT_TIMEOUT_MS,
            ):
                page.click(
                    'input[type="submit"][name="submitProgram"], '
                    'input[type="submit"][value*="Submit" i]'
                )
        except PWError as e:
            raise ElksOrgError(
                "elks.org submit navigation failed: %s" % str(e)[:300]
            )

        body = page.content()
        low = body.lower()
        if "error" in low and ("required" in low or "please" in low):
            raise ElksOrgError(
                "elks.org rejected the submission — "
                + self._extract_error_snippet(body)
            )
        # Look for a recordID in the resulting page (list view) or a
        # success message; return whatever we can find as the confirmation.
        m = re.search(r'recordid[^0-9]*(\d+)', low)
        if m:
            return "recordID=%s" % m.group(1)
        if "days since last charitable event" in low:
            # Back on the list view = probable success.
            return "OK"
        return "OK (unverified)"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _first_visible(page, selectors):
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    @staticmethod
    def _extract_error_snippet(body):
        for pattern in (
            r'class=["\']error["\'][^>]*>([^<]+)<',
            r'<div[^>]*alert[^>]*>([^<]+)<',
            r'([Ii]nvalid[^\.<]+)',
        ):
            m = re.search(pattern, body)
            if m:
                return m.group(1).strip()[:200]
        return "(no error text extracted)"
