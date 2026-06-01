/** @odoo-module **/
/*
 * Elks Charity Impact — public website snippet renderer.
 *
 * For each .elks-charity-mount on the page, fetches aggregated
 * totals from /elks-charity/website/totals.json and renders hero
 * numbers + per-category cards into the mount div.
 *
 * Matches the elks_calendar_publisher pattern: tiny mount div in the
 * snippet template, everything else built here so the website builder
 * preview is safe.
 */
import publicWidget from "@web/legacy/js/public/public_widget";

const fmtNumber = (n) => {
    if (n === null || n === undefined) return "0";
    return Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
};

const fmtMoney = (n, currency) => {
    const sym = (currency && currency.symbol) || "$";
    const pos = (currency && currency.position) || "before";
    const v = fmtNumber(n);
    return pos === "after" ? `${v} ${sym}` : `${sym}${v}`;
};

const trendColor = (delta) => {
    if (delta > 0) return "text-success";
    if (delta < 0) return "text-danger";
    return "text-muted";
};

const trendArrow = (delta) => {
    if (delta > 0) return "▲";
    if (delta < 0) return "▼";
    return "—";
};

const escapeHtml = (s) => {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
};

const heroBlock = (label, valueText, deltaText, deltaColor) => `
    <div class="col-6 col-md-3">
        <div class="s_elks_charity_metric text-center">
            <div class="s_elks_charity_metric_value">${valueText}</div>
            <div class="s_elks_charity_metric_label">${escapeHtml(label)}</div>
            <div class="s_elks_charity_metric_delta ${deltaColor || "text-muted"}">${deltaText || ""}</div>
        </div>
    </div>
`;

const heroDelta = (pct) => {
    if (pct === null || pct === undefined) return ["", "text-muted"];
    const sign = pct > 0 ? "+" : "";
    return [
        `${trendArrow(pct)} ${sign}${pct.toFixed(0)}% vs. last year`,
        trendColor(pct),
    ];
};

const cardBlock = (c, currency) => {
    const dh = c.delta_hours || 0;
    const dc = c.delta_cash || 0;
    const hoursDelta = dh
        ? `<div class="small ${trendColor(dh)}">${trendArrow(dh)} ${fmtNumber(Math.abs(dh))}</div>`
        : "";
    const cashDelta = dc
        ? `<div class="small ${trendColor(dc)}">${trendArrow(dc)} ${fmtMoney(Math.abs(dc), currency)}</div>`
        : "";
    return `
        <div class="col-12 col-sm-6 col-lg-4">
            <div class="s_elks_charity_card h-100">
                <div class="s_elks_charity_card_header">
                    <span class="s_elks_charity_card_code">${escapeHtml(c.code || "")}</span>
                    <span class="s_elks_charity_card_name">${escapeHtml(c.name || "")}</span>
                </div>
                <div class="s_elks_charity_card_section text-muted">${escapeHtml(c.section || "")}</div>
                <div class="row mt-3 g-2">
                    <div class="col-6">
                        <div class="s_elks_charity_card_label">Hours</div>
                        <div class="s_elks_charity_card_value">${fmtNumber(c.hours)}</div>
                        ${hoursDelta}
                    </div>
                    <div class="col-6">
                        <div class="s_elks_charity_card_label">$ Raised</div>
                        <div class="s_elks_charity_card_value">${fmtMoney(c.cash, currency)}</div>
                        ${cashDelta}
                    </div>
                </div>
                <div class="row mt-2 g-2">
                    <div class="col-6">
                        <div class="s_elks_charity_card_label">In-Kind</div>
                        <div class="s_elks_charity_card_minor">${fmtMoney(c.non_cash, currency)}</div>
                    </div>
                    <div class="col-6">
                        <div class="s_elks_charity_card_label">People Served</div>
                        <div class="s_elks_charity_card_minor">${fmtNumber(c.people_served)}</div>
                    </div>
                </div>
            </div>
        </div>
    `;
};

const render = (mountEl, data) => {
    const currency = data.currency || { symbol: "$", position: "before" };
    const t = data.totals || {};
    const pct = data.pct || {};
    const cards = data.cards || [];

    const [dHours, cHours] = heroDelta(pct.hours);
    const [dCash, cCash] = heroDelta(pct.cash);
    const [dNonCash, cNonCash] = heroDelta(pct.non_cash);
    const [dPeople, cPeople] = heroDelta(pct.people_served);

    const subtitle = data.current_year
        ? `Lodge year <strong>${escapeHtml(data.current_year)}</strong>${
              data.prior_year ? " vs. " + escapeHtml(data.prior_year) : ""
          } — totals from validated records only`
        : "Totals from validated records";

    const cardsHtml = cards.length
        ? `<div class="row mb-3"><div class="col-12">
             <h3 class="h4 mb-3">By Charity Category</h3>
           </div></div>
           <div class="row g-3">${cards.map((c) => cardBlock(c, currency)).join("")}</div>`
        : `<div class="row"><div class="col-12 text-center text-muted py-4">
             <em>No validated charity activity to display yet for this lodge year.</em>
           </div></div>`;

    mountEl.innerHTML = `
        <div class="row mb-4">
            <div class="col-12 text-center">
                <h2 class="display-5 mb-2">Our Community Impact</h2>
                <p class="lead text-muted mb-0">${subtitle}</p>
            </div>
        </div>
        <div class="row g-3 mb-5 s_elks_charity_hero">
            ${heroBlock("Volunteer Hours", fmtNumber(t.hours), dHours, cHours)}
            ${heroBlock("Cash Raised", fmtMoney(t.cash, currency), dCash, cCash)}
            ${heroBlock("In-Kind Value", fmtMoney(t.non_cash, currency), dNonCash, cNonCash)}
            ${heroBlock("People Served", fmtNumber(t.people_served), dPeople, cPeople)}
        </div>
        ${cardsHtml}
    `;
};

publicWidget.registry.ElksCharityImpact = publicWidget.Widget.extend({
    selector: ".elks-charity-mount",

    async start() {
        const mount = this.el;
        try {
            const resp = await fetch("/elks-charity/website/totals.json", {
                headers: { Accept: "application/json" },
            });
            if (!resp.ok) {
                mount.innerHTML = `<p class="text-center text-danger">Charity totals unavailable.</p>`;
                return;
            }
            const data = await resp.json();
            render(mount, data);
        } catch (e) {
            console.warn("Elks Charity Impact: data fetch failed", e);
            mount.innerHTML = `<p class="text-center text-danger">Charity totals unavailable.</p>`;
        }
    },
});

export default publicWidget.registry.ElksCharityImpact;
