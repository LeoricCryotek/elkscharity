/** @odoo-module **/
/*
 * =============================================================================
 * === HUMAN ===
 * Public "Volunteer Hours Leaderboard" renderer. For each .elks-leaderboard-mount
 * on the page it fetches the ranked hours from the server and draws two boards —
 * This Month and This Lodge Year — with the top volunteer featured large and
 * places 2–10 listed below, plus the note on our duty to serve.
 *
 * === AI AGENT ===
 * Same shape as the Charity Impact snippet: tiny mount div in the template,
 * everything built here so the website builder preview is safe. Data is the
 * shared elks.charity.leaderboard model via /elks-charity/website/leaderboard.json.
 * All member names are escaped before insertion.
 * =============================================================================
 */
import publicWidget from "@web/legacy/js/public/public_widget";

const fmtHours = (n) => {
    const v = Number(n || 0);
    // Whole numbers print clean; fractional hours keep one decimal.
    return (Math.round(v * 10) % 10 === 0 ? v.toFixed(0) : v.toFixed(1));
};

const escapeHtml = (s) => {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
};

const medal = (rank) => (rank === 1 ? "🥇" : rank === 2 ? "🥈" : rank === 3 ? "🥉" : "");

// The #1 volunteer, featured large.
const championBlock = (row) => {
    if (!row) {
        return "";
    }
    return `
        <div class="s_elks_lb_champion">
            <div class="s_elks_lb_champion_crown">🏆</div>
            <div class="s_elks_lb_champion_name">${escapeHtml(row.name)}</div>
            <div class="s_elks_lb_champion_hours">
                <span class="s_elks_lb_champion_num">${fmtHours(row.hours)}</span>
                <span class="s_elks_lb_champion_unit">hours</span>
            </div>
            <div class="s_elks_lb_champion_tag">Top Volunteer</div>
        </div>
    `;
};

// Places 2..N as a compact ranked list.
const runnersUp = (rows) => {
    const rest = (rows || []).slice(1);
    if (!rest.length) {
        return "";
    }
    const items = rest
        .map(
            (r) => `
        <li class="s_elks_lb_row">
            <span class="s_elks_lb_rank">${r.rank}</span>
            <span class="s_elks_lb_name">${medal(r.rank)} ${escapeHtml(r.name)}</span>
            <span class="s_elks_lb_hours">${fmtHours(r.hours)} <small>hrs</small></span>
        </li>`
        )
        .join("");
    return `<ol class="s_elks_lb_list">${items}</ol>`;
};

const board = (title, subtitle, rows) => {
    const body = (rows && rows.length)
        ? `${championBlock(rows[0])}${runnersUp(rows)}`
        : `<p class="text-center text-muted s_elks_lb_empty"><em>No volunteer hours logged yet for this period.</em></p>`;
    return `
        <div class="col-12 col-lg-6">
            <div class="s_elks_lb_board">
                <div class="s_elks_lb_board_head">
                    <h3 class="s_elks_lb_board_title">${escapeHtml(title)}</h3>
                    <div class="s_elks_lb_board_sub">${escapeHtml(subtitle)}</div>
                </div>
                ${body}
            </div>
        </div>
    `;
};

const render = (mount, data) => {
    const month = data.month || [];
    const year = data.lodge_year || [];
    const note = data.note || "";
    mount.innerHTML = `
        <div class="row mb-4">
            <div class="col-12 text-center">
                <h2 class="display-6 mb-1">Volunteer Hours Leaderboard</h2>
                <p class="lead text-muted mb-0">Celebrating the Elks who give their time</p>
            </div>
        </div>
        <div class="row g-4">
            ${board("This Month", data.month_label || "", month)}
            ${board("This Lodge Year", data.year_label || "", year)}
        </div>
        ${note ? `<div class="row mt-4"><div class="col-12"><div class="s_elks_lb_note">${escapeHtml(note)}</div></div></div>` : ""}
    `;
};

publicWidget.registry.ElksLeaderboard = publicWidget.Widget.extend({
    selector: ".elks-leaderboard-mount",

    async start() {
        const mount = this.el;
        const limit = parseInt(mount.dataset.limit, 10) || 10;
        try {
            const resp = await fetch(
                `/elks-charity/website/leaderboard.json?limit=${limit}`,
                { headers: { Accept: "application/json" } }
            );
            if (!resp.ok) {
                mount.innerHTML = `<p class="text-center text-danger">Leaderboard unavailable.</p>`;
                return;
            }
            const data = await resp.json();
            render(mount, data);
        } catch (e) {
            console.warn("Elks Leaderboard: data fetch failed", e);
            mount.innerHTML = `<p class="text-center text-danger">Leaderboard unavailable.</p>`;
        }
    },
});

export default publicWidget.registry.ElksLeaderboard;
