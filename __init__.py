# -*- coding: utf-8 -*-
from . import models
from . import wizard
from . import controllers
from . import services


def alphabetize_app_menus(env):
    """Sort every root app-launcher menu alphabetically by name.

    Mirrors the "Alphabetize App Menus" tool that ships with the
    elkssecretary module — but here it fires automatically on install
    and on every module upgrade (via post_init_hook and the latest
    version's post-migrate.py respectively).  Idempotent — safe to
    run any number of times.  Applies to ALL root menus, not just
    the ones this module ships, so the app grid stays alphabetized
    across the whole database.
    """
    root_menus = env["ir.ui.menu"].sudo().search(
        [("parent_id", "=", False)],
        order="name asc",
    )
    for i, menu in enumerate(root_menus, start=1):
        # Space out sequences by 10 so a Secretary can slot a custom
        # menu in between two without renumbering everything.
        if menu.sequence != i * 10:
            menu.sequence = i * 10


def _post_init_hook(env):
    """Runs once on FIRST install of this module."""
    alphabetize_app_menus(env)
