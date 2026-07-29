# -*- coding: utf-8 -*-
# =============================================================================
# === HUMAN ===
# Empty tombstone for the old server-side elks.org push client.  Prior
# versions (19.0.2.x) ran headless Chromium here to log in to elks.org
# and post charity contributions server-side.  Elks.org's bot detection
# broke that approach for good in mid-2026, so the whole flow moved to
# the Chrome extension in extension/.  This file stays as a stub so any
# straggler import doesn't ImportError; instantiating the client raises
# a clear "removed in 19.0.5.0" error pointing to the extension.
#
# === AI AGENT ===
# Do not resurrect this module.  All elks.org submission logic lives in
# extension/background.js + controllers/elks_org_extension.py.  Both
# classes here are kept to preserve the module-level symbols
# (ElksOrgError, ElksOrgClient) for any external caller that still
# imports them.  ElksOrgClient.__init__ raises NotImplementedError.
# =============================================================================
"""Tombstone module — see file header."""


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
