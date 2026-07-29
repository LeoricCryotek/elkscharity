# -*- coding: utf-8 -*-
"""REMOVED in 19.0.5.0 — was the server-side Playwright client for the
elks.org auto-push.  Replaced entirely by the Chrome extension flow
(see extension/ + controllers/elks_org_extension.py) after elks.org's
bot detection made the automated login path unmaintainable.

Kept as an empty module so any stray import doesn't ImportError; the
symbols are still exposed but raise if instantiated.
"""


class ElksOrgError(Exception):
    """Kept for legacy import compatibility only."""


class ElksOrgClient:
    """Legacy Playwright client — removed in 19.0.5.0."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "The server-side elks.org push was removed in 19.0.5.0.  "
            "Use the Elks.org Push Chrome extension instead — "
            "Elks Charity → Configuration → Elks.org Push Setup."
        )
