// ============================================================================
// Elks.org Push — popup UI
// ============================================================================
// Loads current settings, lets the user save + toggle + run-now,
// and displays the last-known status from the service worker.
// All actual work happens in background.js — the popup is a thin
// controller.
// ============================================================================

const $ = (id) => document.getElementById(id);

async function loadSettings() {
    return new Promise((res) =>
        chrome.storage.local.get(
            ["odooUrl", "apiKey", "enabled", "dryRun", "lastStatus"],
            (v) => res(v || {}),
        )
    );
}

// Basic HTML-escape so error text can't inject markup.
function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => (
        { "&": "&amp;", "<": "&lt;", ">": "&gt;",
          '"': "&quot;", "'": "&#39;" }[c]
    ));
}

async function renderStatus() {
    const {lastStatus, odooUrl} = await loadSettings();
    const box = $("status");
    box.className = "";
    if (!lastStatus) {
        box.innerHTML =
            '<div class="status-state">Idle</div>' +
            '<div class="muted">No poll cycles run yet.</div>';
        return;
    }
    box.className = lastStatus.state || "";
    let html =
        '<div class="status-state">' +
            esc((lastStatus.state || "unknown").replace(/_/g, " ")) +
        '</div>' +
        '<div>' + esc(lastStatus.message || "") + '</div>';
    if (lastStatus.updated) {
        html += '<div class="muted" style="margin-top:4px;">Updated ' +
                esc(new Date(lastStatus.updated).toLocaleTimeString()) +
                '</div>';
    }
    // If there were failures, expose the last-error record + link to
    // the Failed Pushes list in Odoo so the Secretary can drill in.
    if (lastStatus.lastError && (lastStatus.lastFailure || 0) > 0) {
        const err = lastStatus.lastError;
        const base = (odooUrl || "").replace(/\/+$/, "");
        const recordLink = base
            ? `${base}/odoo/action-elkscharity.action_charity_contributions_failed_push`
            : "";
        html += '<div style="margin-top:6px; padding:6px 8px; ' +
                'background:#fff5f5; border:1px solid #f5c6cb; ' +
                'border-radius:3px; font-size:11px;">' +
                '<strong>Last failure:</strong> ' +
                esc(err.name || ("#" + err.id)) +
                '<br/><span style="color:#7a1e1e;">' +
                esc((err.message || "").slice(0, 200)) +
                '</span>';
        if (recordLink) {
            html += `<br/><a href="${esc(recordLink)}" target="_blank" ` +
                    'style="color:#6a2020;">Open Failed Pushes in Odoo →</a>';
        }
        html += '</div>';
    }
    box.innerHTML = html;
}

async function init() {
    const s = await loadSettings();
    $("odoo-url").value = s.odooUrl || "";
    $("api-key").value  = s.apiKey  || "";
    $("enabled").checked = !!s.enabled;
    $("dryRun").checked = !!s.dryRun;
    await renderStatus();
    await refreshPendingCount();

    // Auto-refresh status + pending count every 3 seconds while
    // popup is open.
    setInterval(async () => {
        await renderStatus();
        await refreshPendingCount();
    }, 3000);
}

// Live count of pending pushes so the Secretary can see whether
// there's anything to push BEFORE they click Push Now.
async function refreshPendingCount() {
    const s = await loadSettings();
    if (!s.odooUrl || !s.apiKey) {
        $("pending-count").textContent = "(configure Odoo URL + Key)";
        return;
    }
    try {
        const url = s.odooUrl.replace(/\/+$/, "")
                  + "/elkscharity/ext/v1/pending";
        const resp = await fetch(url, {
            method: "GET",
            headers: {"X-Elks-Api-Key": s.apiKey},
            credentials: "omit",
        });
        if (resp.status === 401) {
            $("pending-count").textContent = "(invalid API key)";
            return;
        }
        const body = await resp.json();
        // total_pending is the true queue depth across all rows.
        // count is the per-poll batch size cap (25) — shown in
        // parens if it's smaller than the total so the user knows
        // multiple polls will process everything.
        const total = (body && body.total_pending) ?? (body && body.count) ?? 0;
        const batch = (body && body.count) || 0;
        if (total === 0) {
            $("pending-count").textContent = "0 (queue empty)";
        } else if (batch < total) {
            $("pending-count").textContent =
                total + " total (" + batch + " per poll)";
        } else {
            $("pending-count").textContent = String(total);
        }
    } catch (e) {
        $("pending-count").textContent = "(couldn't reach Odoo)";
    }
}

$("save").addEventListener("click", async () => {
    const odooUrl = $("odoo-url").value.trim();
    const apiKey = $("api-key").value.trim();
    const enabled = $("enabled").checked;
    const dryRun = $("dryRun").checked;
    if (odooUrl && !/^https?:\/\//i.test(odooUrl)) {
        alert("Odoo URL must start with http:// or https://");
        return;
    }
    // If the connection target changed (URL or API key), wipe the
    // last-known status from the previous environment.  Otherwise the
    // popup shows a stale "DRY RUN — N pushed" from the old server
    // when the user moves the extension from a test Odoo to prod.
    const prev = await loadSettings();
    const targetChanged = (
        prev.odooUrl !== odooUrl || prev.apiKey !== apiKey
    );
    const patch = {odooUrl, apiKey, enabled, dryRun};
    if (targetChanged) {
        patch.lastStatus = null;
    }
    chrome.storage.local.set(patch, async () => {
        $("save").textContent = "Saved ✓";
        setTimeout(() => {
            $("save").textContent = "Save Settings";
        }, 1500);
        if (targetChanged) {
            await renderStatus();
            await refreshPendingCount();
        }
    });
});

$("run-now").addEventListener("click", async () => {
    // Push Now bypasses the "Enabled" toggle by force-enabling for
    // this single cycle.  It's a manual override — the user asked
    // for it explicitly.
    const s = await loadSettings();
    if (!s.enabled) {
        // Temporarily enable for this one run so the background
        // service worker actually does something (it checks enabled
        // at the top of its cycle).  Restore afterwards.
        await new Promise((r) =>
            chrome.storage.local.set({enabled: true}, () => r())
        );
    }
    $("run-now").textContent = "Running…";
    chrome.runtime.sendMessage({type: "run_now"}, async (resp) => {
        $("run-now").textContent = "Push Now";
        // Restore original enabled state.
        if (!s.enabled) {
            await new Promise((r) =>
                chrome.storage.local.set({enabled: false}, () => r())
            );
        }
        setTimeout(async () => {
            await renderStatus();
            await refreshPendingCount();
        }, 500);
    });
});

$("test-odoo").addEventListener("click", async () => {
    $("test-odoo").textContent = "Testing…";
    chrome.runtime.sendMessage({type: "test_whoami"}, (resp) => {
        $("test-odoo").textContent = "Test Odoo Key";
        if (!resp) return;
        if (!resp.ok) {
            alert("Odoo test failed: " + resp.error);
            return;
        }
        const r = resp.result;
        if (r.status === 200) {
            alert(
                "OK — connected as " + (r.json.user_name || "?") +
                " (login: " + (r.json.user_login || "?") + ").",
            );
        } else if (r.status === 401) {
            alert("Odoo rejected the API key.  Regenerate + paste again.");
        } else {
            alert(
                "Unexpected HTTP " + r.status + ".\n\nBody:\n" +
                (r.text || "").slice(0, 400),
            );
        }
    });
});

$("preview-pending").addEventListener("click", async () => {
    // Open Odoo's "Pending Elks.org Push" list in a new tab so the
    // Secretary can inspect exactly what will get submitted.  The
    // list is a plain Odoo list view — Odoo's own auth applies (the
    // Secretary must already be logged in to Odoo).  We can't pass
    // our extension API key into an Odoo backend URL — those are
    // cookie-authenticated for humans.
    const s = await loadSettings();
    if (!s.odooUrl) {
        alert("Set Odoo URL first, then click Save.");
        return;
    }
    // Deep link into the act_window via xml_id.  Odoo's /odoo route
    // resolves ir.actions.act_window xmlids in the URL fragment.
    const base = s.odooUrl.replace(/\/+$/, "");
    const url = base + "/odoo/action-elkscharity.action_charity_contributions_pending_push";
    chrome.tabs.create({url: url});
});

$("test-elks").addEventListener("click", async () => {
    $("test-elks").textContent = "Testing…";
    chrome.runtime.sendMessage({type: "test_elks_session"}, (resp) => {
        $("test-elks").textContent = "Test Elks.org Session";
        if (!resp) return;
        if (!resp.ok) {
            alert("Elks test failed: " + resp.error);
            return;
        }
        const r = resp.result;
        if (r.ok) {
            alert("Elks.org session is ACTIVE.  Landed at " + r.url);
        } else {
            alert(
                "No active elks.org session.  Open elks.org and " +
                "log in, then try again.  Landed at " + r.url,
            );
        }
    });
});

init();
