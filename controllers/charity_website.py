# -*- coding: utf-8 -*-
"""Public JSON endpoint that feeds the Charity Impact website snippet.

The snippet itself renders only static placeholder HTML at page load
(safe to preview in the website builder).  After the page mounts, a
small frontend JS module hits this endpoint, gets aggregated totals,
and fills in the cards client-side.

Public by design — uses sudo() and returns AGGREGATES ONLY (no
employee names, no per-record data).
"""
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ElksCharityWebsite(http.Controller):

    @http.route(
        "/elks-charity/website/totals.json",
        type="http",
        auth="public",
        website=True,
        sitemap=False,
        csrf=False,
        methods=["GET"],
    )
    def charity_totals_json(self, lodge_year=None, **kw):
        """Return the public charity totals as JSON.

        Query params:
            lodge_year — optional 'YYYY-YYYY' string.  Defaults to the
                         current lodge year on the server.
        """
        try:
            data = (
                request.env["elks.charity.dashboard"]
                .sudo()
                ._website_totals(lodge_year=lodge_year)
            )
        except Exception:
            # Never let a snippet-data failure break the public page.
            # Return zeros so the snippet renders empty rather than
            # showing a broken state.
            _logger.exception(
                "elkscharity website snippet: totals fetch failed"
            )
            data = {
                "current_year": "",
                "prior_year": "",
                "totals": {"hours": 0, "cash": 0, "non_cash": 0,
                           "people_served": 0},
                "prior": {"hours": 0, "cash": 0, "non_cash": 0,
                          "people_served": 0},
                "deltas": {"hours": 0, "cash": 0, "non_cash": 0,
                           "people_served": 0},
                "pct": {"hours": None, "cash": None, "non_cash": None,
                        "people_served": None},
                "cards": [],
                "currency": None,
            }

        # The currency value in _website_totals is a recordset; convert
        # to a plain symbol + position dict for the JSON envelope.
        cur = data.get("currency")
        if cur:
            data["currency"] = {
                "symbol": cur.symbol or "$",
                "position": cur.position or "before",
            }
        else:
            data["currency"] = {"symbol": "$", "position": "before"}

        return request.make_json_response(data)
