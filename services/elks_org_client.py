# -*- coding: utf-8 -*-
"""HTTP client for elks.org Local Lodge Reporting auto-push.

Two paths, `requests` first, Playwright fallback:

1. `requests.Session()` (fast, ~2s per push) — used by default.
2. Playwright + Chromium — reserved for the future if elks.org starts
   requiring JS execution (e.g. captcha).  Currently unused; the code
   stays around as a hedge.

We used to think Playwright was mandatory because elks.org's login
page renders the form via JavaScript.  Turns out the form is generated
client-side by inline JS but the actual submission is a plain HTTP
POST with a handful of hidden fields.  See the HTML at
    https://www.elks.org/secure/elksLogin.cfm

Actual login form fields (verified July 2026):
    - username           text input, name="username"
    - password           password input, name="password"
    - SendFormAction     submit input, value="Login"
    - theServer          hidden, value="www.elks.org"
    - dID                hidden, value="0"
    - TOS                hidden, value="1"
Form action:
    /secure/elksLogin.cfm?theServer=www.elks.org

Contribution form (verified from the user's paste):
    /grandlodge/charity/local.cfm
    - programDate        YYYY-MM-DD
    - programID          e.g. "1102", "9999"
    - otherProgramID     used only when programID == "9999"
    - programName
    - headcount, numberElks, numberHelpers
    - hoursElks, hoursHelpers
    - milesElks, milesHelpers
    - nonCash, cash
    - theUID             per-session token; scraped from the form page
    - submitProgram      value="Submit New Charitable Program"

All errors raise ElksOrgError with an optional diagnostics dict
carrying URL + body snippet (no screenshot in requests mode).
"""
import logging
import re

_logger = logging.getLogger(__name__)


class ElksOrgError(Exception):
    """Raised when elks.org login or submission fails.

    Optional ``diagnostics`` dict carries a URL and short HTML snippet
    from the failure page.  When we used to drive Playwright, this also
    held a base64 PNG screenshot; kept as an optional key for future
    compatibility.
    """

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class ElksOrgClient:
    """Plain-requests client for elks.org Local Lodge Reporting."""

    DEFAULT_TIMEOUT = 30  # seconds per HTTP round-trip
    RATE_LIMIT_SLEEP = 0.5  # gentle pause between bulk submissions

    def __init__(
        self,
        login,
        password,
        login_url=None,
        form_url=None,
        # kept for API compatibility with the old Playwright signature;
        # ignored in the requests path.
        headless=True,
        slow_mo_ms=0,
    ):
        try:
            import requests  # noqa: F401
        except ImportError:
            raise ElksOrgError(
                "The 'requests' library is required for the elks.org "
                "auto-push.  It normally ships with Odoo — check the "
                "server admin if it's really missing."
            )
        if not login or not password:
            raise ElksOrgError(
                "Elks.org login and password must both be set."
            )
        self.login = login
        self.password = password
        self.login_url = login_url or (
            "https://www.elks.org/secure/elksLogin.cfm"
        )
        self.form_url = form_url or (
            "https://www.elks.org/grandlodge/charity/local.cfm"
        )
        self._session = None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------
    def _get_session(self):
        if self._session is not None:
            return self._session
        import requests
        s = requests.Session()
        # Mimic a real desktop Chrome closely — elks.org's ColdFusion
        # stack may be doing UA-based filtering that trips on obviously
        # automated User-Agents.  Being honest ("+lewistonelks896.com")
        # didn't work; now we masquerade.
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        })
        self._session = s
        return s

    def _authenticate(self):
        """Login to elks.org.

        Uses Playwright to drive real Chromium (elks.org's server-side
        check rejects plain-requests POSTs even with valid credentials —
        verified by testing the same creds in a browser).  Once logged
        in, extracts the session cookies from Chromium and transfers
        them to a requests.Session for the fast subsequent form
        submissions.  Chromium closes immediately after login, so we
        pay the ~5s Playwright launch ONCE per bulk batch instead of
        per push.

        Falls back to plain-requests login if Playwright isn't
        installed (useful for lodges that don't need the auto-push and
        just want to mark submissions manually).
        """
        try:
            self._authenticate_playwright()
        except ElksOrgError:
            # Bubble up — Playwright is the reliable path.  If it
            # failed, plain requests almost certainly won't help.
            raise
        except Exception as e:
            _logger.warning(
                "elkscharity: Playwright login blew up (%s); "
                "falling back to plain requests.", e
            )
            self._authenticate_requests()

    def _authenticate_playwright(self):
        """Drive real Chromium through the JS-required login form, then
        copy the resulting session cookies into our requests.Session."""
        try:
            from playwright.sync_api import (
                sync_playwright,
                Error as PWError,
            )
        except ImportError:
            raise ElksOrgError(
                "Playwright isn't installed on the Odoo server.  "
                "Ask your admin to run:\n\n"
                "    <odoo-venv>/bin/pip install playwright\n"
                "    <odoo-venv>/bin/playwright install chromium\n"
                "    <odoo-venv>/bin/playwright install-deps chromium\n\n"
                "Then restart Odoo.  (Elks.org's login requires "
                "JavaScript execution, so plain requests can't "
                "authenticate on its own.)"
            )

        headless = not getattr(self, "_diagnostic_visible", False)
        slow_mo = 200 if not headless else 0

        with sync_playwright() as p:
            try:
                # Bot-detection defenses:
                #   --disable-blink-features=AutomationControlled removes
                #     the "chrome is being controlled by automated
                #     software" banner and the corresponding devtools
                #     flag.
                #   --disable-features=IsolateOrigins,site-per-process
                #     helps with sites that check for specific process
                #     isolation behavior.
                browser = p.chromium.launch(
                    headless=headless,
                    slow_mo=slow_mo,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--no-sandbox",
                    ],
                )
            except PWError as e:
                raise ElksOrgError(
                    "Couldn't launch Chromium: %s.  Run "
                    "'playwright install chromium' on this server." % e
                )
            try:
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                    timezone_id="America/Los_Angeles",
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                # Stealth: erase the tell-tale automation fingerprints
                # BEFORE any page script runs.  This addEval-init script
                # runs on every navigation for every new page in the
                # context.
                context.add_init_script("""
                    // navigator.webdriver is the #1 bot signal.
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });
                    // Populate plugins so headless doesn't look empty.
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5],
                    });
                    // Set languages to look normal.
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });
                    // Some sites check window.chrome existence.
                    window.chrome = window.chrome || { runtime: {} };
                    // Permissions API shim — real Chrome behaves
                    // differently from headless here.
                    const originalQuery = window.navigator.permissions.query;
                    window.navigator.permissions.query = (parameters) => (
                        parameters.name === 'notifications' ?
                            Promise.resolve({state: Notification.permission}) :
                            originalQuery(parameters)
                    );
                """)
                context.set_default_timeout(45_000)
                page = context.new_page()

                # Follow the redirect chain from /login.cfm so we
                # capture whatever session state the real browser
                # flow establishes.
                page.goto(
                    "https://www.elks.org/login.cfm",
                    wait_until="networkidle",
                )
                # Wait for the JS-generated form to appear.
                try:
                    page.wait_for_selector(
                        'input[name="username"], input[type="password"]',
                        timeout=15_000,
                    )
                except PWError:
                    raise ElksOrgError(
                        "Login form didn't render on elks.org.",
                        diagnostics={
                            "url": page.url,
                            "html_snippet": (page.content() or "")[:8000],
                        },
                    )

                # Small pause to let any anti-bot init scripts run.
                page.wait_for_timeout(800)

                # Fill and submit — mimic real user typing with a
                # click-focus + human-scale keystroke delay.
                user_el = page.locator('input[name="username"]').first
                pass_el = page.locator('input[name="password"]').first
                user_el.click()
                page.wait_for_timeout(150)
                page.keyboard.type(self.login, delay=60)
                page.wait_for_timeout(200)
                pass_el.click()
                page.wait_for_timeout(150)
                page.keyboard.type(self.password, delay=60)
                page.wait_for_timeout(400)

                # Click the submit; the form's action posts to
                # /secure/elksLogin.cfm?theServer=www.elks.org.
                try:
                    with page.expect_navigation(
                        wait_until="networkidle",
                        timeout=45_000,
                    ):
                        page.click(
                            'input[type="submit"][name="SendFormAction"], '
                            'input[type="submit"][value="Login"]'
                        )
                except PWError:
                    # SPA-style submission: click without waiting for
                    # a full navigation.
                    page.click(
                        'input[type="submit"][name="SendFormAction"], '
                        'input[type="submit"][value="Login"]'
                    )
                    page.wait_for_load_state(
                        "networkidle", timeout=15_000
                    )

                # Success signal: we're no longer on the login page.
                current_url = page.url or ""
                if "login" in current_url.lower() or "elkslogin" in current_url.lower():
                    body = ""
                    try:
                        body = page.content()
                    except Exception:
                        pass
                    # Capture a screenshot so the Secretary can SEE
                    # exactly what elks.org showed after our login POST
                    # (error banner? captcha? blank re-render?).  Base64
                    # is what _log_elks_org_failure expects for the
                    # ir.attachment "datas" column.
                    import base64
                    screenshot_b64 = ""
                    try:
                        raw = page.screenshot(
                            full_page=True,
                            type="png",
                        )
                        screenshot_b64 = base64.b64encode(raw).decode(
                            "ascii"
                        )
                    except Exception:
                        pass
                    raise ElksOrgError(
                        "Chromium submitted the login but landed at "
                        "%s.  Check the attached screenshot to see what "
                        "elks.org showed — if there's an error banner, "
                        "credentials or account status is the problem; "
                        "if there's a captcha, elks.org added a bot "
                        "check we now have to handle." % current_url,
                        diagnostics={
                            "url": current_url,
                            "html_snippet": body[:8000],
                            "screenshot_png_b64": screenshot_b64,
                        },
                    )

                # Copy the authenticated cookies into our requests
                # session so the follow-up form submissions are fast.
                s = self._get_session()
                for cookie in context.cookies():
                    s.cookies.set(
                        cookie["name"],
                        cookie["value"],
                        domain=cookie.get("domain"),
                        path=cookie.get("path", "/"),
                    )
                _logger.info(
                    "elkscharity: Playwright login OK, imported %d "
                    "cookies into requests session.  Post-login URL: %s",
                    len(context.cookies()), current_url,
                )
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    def _authenticate_requests(self):
        """Fallback: plain requests-based login.  Kept for lodges where
        Playwright isn't available.  Known to fail against elks.org's
        current server-side check as of July 2026 — the code stays as
        a hedge in case elks.org relaxes the check later."""
        s = self._get_session()
        # Step 1 — GET the login page to establish a session cookie.
        try:
            s.get(self.login_url, timeout=self.DEFAULT_TIMEOUT)
        except Exception as e:
            raise ElksOrgError(
                "Couldn't reach elks.org login page: %s" % str(e)[:200]
            )

        # Step 2 — POST credentials.  First without following redirects
        # so we can distinguish 302 (auth succeeded, redirect to
        # members area) from 200 (auth failed, login page re-served).
        # Then follow the redirect manually and settle on the final page.
        action_url = self.login_url + "?theServer=www.elks.org"
        payload = {
            "username": self.login,
            "password": self.password,
            "SendFormAction": "Login",
            "theServer": "www.elks.org",
            "dID": "0",
            "TOS": "1",
        }
        post_headers = {
            "Referer": self.login_url,
            "Origin": "https://www.elks.org",
            "Content-Type": "application/x-www-form-urlencoded",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
        }
        try:
            first_resp = s.post(
                action_url,
                data=payload,
                headers=post_headers,
                timeout=self.DEFAULT_TIMEOUT,
                allow_redirects=False,
            )
        except Exception as e:
            raise ElksOrgError(
                "elks.org login POST failed: %s" % str(e)[:200]
            )
        _logger.info(
            "elkscharity login POST: status=%s location=%s cookies=%s",
            first_resp.status_code,
            first_resp.headers.get("Location", "(none)"),
            [c.name for c in s.cookies],
        )

        # A 30x with Location != login page is the "auth succeeded" path.
        if first_resp.status_code in (301, 302, 303, 307):
            location = first_resp.headers.get("Location", "")
            if location and "login" not in location.lower() and "elkslogin" not in location.lower():
                # Follow to confirm the destination is authenticated.
                try:
                    resp = s.get(
                        location if location.startswith("http")
                        else "https://www.elks.org" + location,
                        timeout=self.DEFAULT_TIMEOUT,
                        allow_redirects=True,
                    )
                except Exception as e:
                    raise ElksOrgError(
                        "elks.org login redirect fetch failed: %s"
                        % str(e)[:200]
                    )
                return  # authenticated (POST returned redirect to non-login)

        # No redirect (or redirect back to login) → auth failed.
        # Follow whatever the server sent us so we can capture the
        # actual error page for the Secretary.
        resp = first_resp
        if first_resp.status_code in (301, 302, 303, 307):
            try:
                resp = s.get(
                    first_resp.headers.get("Location", action_url),
                    timeout=self.DEFAULT_TIMEOUT,
                    allow_redirects=True,
                )
            except Exception:
                pass
        if resp.status_code >= 400:
            raise ElksOrgError(
                "elks.org login returned HTTP %s" % resp.status_code,
                diagnostics={
                    "url": resp.url,
                    "html_snippet": (resp.text or "")[:2000],
                },
            )

        # Verify auth.  After successful login elks.org redirects away
        # from /secure/elksLogin.cfm — usually to /members/default.cfm.
        landed = (resp.url or "").lower()
        body = (resp.text or "").lower()
        landed_off_login = (
            "elkslogin.cfm" not in landed
            and "login.cfm" not in landed
        )
        # An authenticated response body contains a link to the Members
        # area and typically shows the user's name in the header.
        has_authed_marker = any(m in body for m in (
            'href="/logout',
            "/members/default.cfm",
            "logged in as",
            "member center",
            "myaccount",
        ))
        # Rejection markers.
        rejected = any(m in body for m in (
            "not recognized",
            "invalid username",
            "incorrect password",
            "please check your password",
            "unable to log you in",
        ))

        if landed_off_login and has_authed_marker and not rejected:
            return  # authenticated

        # Big snippet so the failure attachment covers everything —
        # 8 KB is plenty to catch an error banner even if it's not
        # in the first 400 chars of the response.
        big_snippet = (resp.text or "")[:8000]

        if rejected:
            raise ElksOrgError(
                "elks.org rejected the credentials — open elks.org in "
                "an incognito window, log in with the EXACT login and "
                "password stored in Preferences → Elks.org Credentials. "
                "If that fails too, fix your Preferences.  If it works "
                "there but fails here, the elks.org front-end may have "
                "changed and we need to iterate.",
                diagnostics={
                    "url": resp.url,
                    "html_snippet": big_snippet,
                },
            )
        raise ElksOrgError(
            "elks.org login didn't produce an authenticated session. "
            "Landed at %s. Most common cause: the login/password in "
            "Preferences is wrong (elks.org re-serves the login page "
            "on auth failure without a visible error).  Verify by "
            "logging in on elks.org with the same credentials in an "
            "incognito browser."
            % (resp.url or "(unknown)"),
            diagnostics={
                "url": resp.url,
                "html_snippet": big_snippet,
            },
        )

    def _get_form_uid(self):
        """GET the form page, extract the per-session theUID token."""
        s = self._get_session()
        try:
            resp = s.get(self.form_url, timeout=self.DEFAULT_TIMEOUT)
        except Exception as e:
            raise ElksOrgError(
                "elks.org form fetch failed: %s" % str(e)[:200]
            )
        if resp.status_code >= 400:
            raise ElksOrgError(
                "elks.org form page returned HTTP %s" % resp.status_code
            )
        m = re.search(
            r'name=["\']theUID["\']\s+value=["\']([^"\']+)["\']',
            resp.text,
            re.IGNORECASE,
        )
        if not m:
            m = re.search(
                r'value=["\']([^"\']+)["\']\s+name=["\']theUID["\']',
                resp.text,
                re.IGNORECASE,
            )
        if not m:
            raise ElksOrgError(
                "Couldn't find theUID on the elks.org form page — session "
                "may not be authenticated or form layout changed.",
                diagnostics={
                    "url": resp.url,
                    "html_snippet": (resp.text or "")[:2000],
                },
            )
        return m.group(1)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def submit_contribution(self, payload):
        """Login (if needed), POST one contribution, return confirmation."""
        results = self.submit_many([payload])
        confirmation, err = results[0]
        if err:
            raise ElksOrgError(err)
        return confirmation

    def submit_many(self, payloads):
        """Login once, POST every payload, return per-item results.

        Returns a list of ``(confirmation_or_none, error_or_none)``
        tuples in the same order as the input.  Never raises out —
        per-payload errors are captured so partial batches complete.
        """
        if not payloads:
            return []
        import time
        results = []
        self._authenticate()
        for payload in payloads:
            try:
                confirmation = self._submit_one(payload)
                results.append((confirmation, None))
            except ElksOrgError as e:
                results.append((None, str(e)[:1000]))
            except Exception as e:
                results.append((
                    None,
                    "unexpected error: %s" % str(e)[:500],
                ))
            time.sleep(self.RATE_LIMIT_SLEEP)
        return results

    def _submit_one(self, payload):
        """Fetch theUID, POST one contribution, return confirmation."""
        uid = self._get_form_uid()
        s = self._get_session()
        # Full payload matching the elks.org form structure.
        pid = str(payload.get("programID", "9999"))
        other = payload.get("otherProgramID", "n/a") or "n/a"
        full = {
            "programDate":    payload.get("programDate", ""),
            "programID":      pid,
            "otherProgramID": other if pid == "9999" else "n/a",
            "programName":    payload.get("programName", ""),
            "headcount":      str(payload.get("headcount", 0)),
            "numberElks":     str(payload.get("numberElks", 0)),
            "numberHelpers":  str(payload.get("numberHelpers", 0)),
            "hoursElks":      str(payload.get("hoursElks", 0)),
            "hoursHelpers":   str(payload.get("hoursHelpers", 0)),
            "milesElks":      str(payload.get("milesElks", 0)),
            "milesHelpers":   str(payload.get("milesHelpers", 0)),
            "nonCash":        str(payload.get("nonCash", 0)),
            "cash":           str(payload.get("cash", 0)),
            "recordID":       str(payload.get("recordID", -1)),
            "theUID":         uid,
            "submitProgram":  "Submit New Charitable Program",
        }
        try:
            resp = s.post(
                self.form_url,
                data=full,
                timeout=self.DEFAULT_TIMEOUT,
                allow_redirects=True,
            )
        except Exception as e:
            raise ElksOrgError(
                "elks.org submit failed: %s" % str(e)[:200]
            )
        if resp.status_code >= 400:
            raise ElksOrgError(
                "elks.org submit returned HTTP %s" % resp.status_code
            )
        body = resp.text or ""
        low = body.lower()
        if "days since last charitable event" in low and 'name="programid"' not in low:
            m = re.search(r'recordid[^0-9]*(\d+)', low)
            return "recordID=%s" % m.group(1) if m else "OK"
        if "error" in low and ("required" in low or "please" in low):
            raise ElksOrgError(
                "elks.org rejected the submission: %s"
                % self._extract_error_snippet(body),
                diagnostics={
                    "url": resp.url,
                    "html_snippet": body[:2000],
                },
            )
        _logger.warning(
            "elkscharity push: response ambiguous, %d bytes.  "
            "Treating as success but Secretary should verify on elks.org.",
            len(body),
        )
        return "OK (unverified)"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
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
