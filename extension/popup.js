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
            ["odooUrl", "apiKey", "enabled", "dryRun", "lastStatus",
             "purgeStatus"],
            (v) => res(v || {}),
        )
    );
}

// Live purge-progress + results box.  Background writes
// {phase, message, current, total, result} to
// chrome.storage.local.purgeStatus.  Popup renders it INLINE — no
// native alert()/confirm() dialogs anywhere in the purge flow.
async function renderPurgeProgress() {
    const {purgeStatus} = await loadSettings();
    const box = document.getElementById("purge-progress");
    if (!box) return;
    if (!purgeStatus) {
        box.style.display = "none";
        box.innerHTML = "";
        return;
    }
    box.style.display = "block";

    // ── SYNC DONE: combined push+purge results ──────────────────
    if (purgeStatus.phase === "sync_done" && purgeStatus.result) {
        const r = purgeStatus.result;
        const gap = (r.serverAccepted || 0) - (r.verified || 0);
        let warn = "";
        if (gap > 0) {
            warn =
                '<div style="margin-top:6px; padding:6px 8px; ' +
                'background:#fff5f5; border:1px solid #f5c6cb; ' +
                'border-radius:3px; color:#7a1e1e; font-size:11px;">' +
                '⚠ Server accepted ' + gap + ' deletions that did ' +
                'NOT actually remove records. See service-worker ' +
                'console for the discovered delete-field name.' +
                '</div>';
        }
        let errBlock = "";
        const allErrors = [];
        if (r.pushLastError && r.pushLastError.message) {
            allErrors.push("push: " + r.pushLastError.message);
        }
        if (r.purgeErrors && r.purgeErrors.length) {
            for (const e of r.purgeErrors) allErrors.push("purge: " + e);
        }
        if (allErrors.length) {
            errBlock =
                '<div style="margin-top:6px; font-size:11px; ' +
                'color:#7a1e1e;">' +
                '<strong>Errors (' + allErrors.length + '):</strong>' +
                '<div style="max-height:80px; overflow-y:auto; ' +
                'margin-top:2px; padding:4px; background:#fff5f5; ' +
                'border:1px solid #f5c6cb; border-radius:3px; ' +
                'font-family:monospace; font-size:10px; ' +
                'white-space:pre-wrap;">' +
                esc(allErrors.slice(0, 8).join("\n")) +
                (allErrors.length > 8
                    ? "\n… and " + (allErrors.length - 8) + " more"
                    : "") +
                '</div></div>';
        }
        box.innerHTML =
            '<div style="display:flex; justify-content:space-between; ' +
            'align-items:center;">' +
                '<div style="font-weight:600; font-size:11px; ' +
                'text-transform:uppercase; color:#0a5d0a;">' +
                    'Sync complete' +
                '</div>' +
                '<button id="purge-dismiss" style="font-size:10px; ' +
                'padding:2px 6px; background:#fff; border:1px solid ' +
                '#ccc; border-radius:3px; cursor:pointer;">Dismiss' +
                '</button>' +
            '</div>' +
            // Push phase
            '<div style="margin-top:6px; font-weight:600; ' +
            'font-size:11px; color:#6a2020;">Phase 1 — Push</div>' +
            '<table style="width:100%; font-size:12px; ' +
            'border-collapse:collapse;">' +
                purgeRow("Attempted", r.pushAttempted) +
                purgeRow("Pushed", r.pushed,
                    r.pushed > 0 ? "#0a5d0a" : "#222") +
                purgeRow("Failed", r.pushFailed,
                    r.pushFailed > 0 ? "#7a1e1e" : "#0a5d0a") +
            '</table>' +
            // Purge phase
            '<div style="margin-top:8px; font-weight:600; ' +
            'font-size:11px; color:#6a2020;">Phase 2 — Purge</div>' +
            '<table style="width:100%; font-size:12px; ' +
            'border-collapse:collapse;">' +
                purgeRow("Scanned rows", r.scanned) +
                purgeRow("Unique kept", r.kept) +
                purgeRow("Duplicates found", r.duplicatesFound) +
                purgeRow("Verified deleted", r.verified,
                    r.verified > 0 ? "#0a5d0a" : "#222") +
                purgeRow("Still present", r.stillPresent,
                    r.stillPresent > 0 ? "#7a1e1e" : "#0a5d0a") +
            '</table>' +
            warn + errBlock +
            // Include the edit-page HTML sample if discovery failed
            // during Sync's purge phase too.
            (r.editHtmlSample && !r.delFieldName
                ? renderEditHtmlSample({
                    editHtmlSample: r.editHtmlSample,
                    editHtmlSampleId: r.editHtmlSampleId,
                })
                : "");
        const dismiss = document.getElementById("purge-dismiss");
        if (dismiss) {
            dismiss.addEventListener("click", () => {
                chrome.storage.local.set({purgeStatus: null},
                    () => renderPurgeProgress());
            });
        }
        return;
    }

    // ── DONE: render final purge-only results inline ────────────
    if (purgeStatus.phase === "done" && purgeStatus.result) {
        const r = purgeStatus.result;
        const gap = (r.serverAccepted ?? 0) - (r.verified ?? 0);
        let warn = "";
        if (gap > 0) {
            warn =
                '<div style="margin-top:6px; padding:6px 8px; ' +
                'background:#fff5f5; border:1px solid #f5c6cb; ' +
                'border-radius:3px; color:#7a1e1e; font-size:11px;">' +
                '⚠ Server accepted ' + gap + ' deletions that did ' +
                'NOT actually remove records.  Open the extension\'s ' +
                'service-worker console for the discovered ' +
                'delete-field name and Edit-page dump.' +
                '</div>';
        }
        let errBlock = "";
        if (r.errors && r.errors.length) {
            errBlock =
                '<div style="margin-top:6px; font-size:11px; ' +
                'color:#7a1e1e;">' +
                '<strong>Errors (' + r.errors.length + '):</strong>' +
                '<div style="max-height:80px; overflow-y:auto; ' +
                'margin-top:2px; padding:4px; background:#fff5f5; ' +
                'border:1px solid #f5c6cb; border-radius:3px; ' +
                'font-family:monospace; font-size:10px; ' +
                'white-space:pre-wrap;">' +
                esc(r.errors.slice(0, 8).join("\n")) +
                (r.errors.length > 8
                    ? "\n… and " + (r.errors.length - 8) + " more"
                    : "") +
                '</div></div>';
        }
        let delField = "";
        if (r.delFieldName) {
            delField =
                '<div style="margin-top:4px; font-size:10px; ' +
                'color:#666;">Delete field discovered: ' +
                '<code>' + esc(r.delFieldName) + '=' +
                esc(r.delFieldValue || "") + '</code></div>';
        } else if (r.editHtmlSample) {
            // Discovery failed for every record — render the sample
            // so the user can copy + share it.
            delField = renderEditHtmlSample(r);
        }
        box.innerHTML =
            '<div style="display:flex; justify-content:space-between; ' +
            'align-items:center;">' +
                '<div style="font-weight:600; font-size:11px; ' +
                'text-transform:uppercase; color:#6a2020;">' +
                    'Purge complete' +
                '</div>' +
                '<button id="purge-dismiss" style="font-size:10px; ' +
                'padding:2px 6px; background:#fff; border:1px solid ' +
                '#ccc; border-radius:3px; cursor:pointer; flex:0;">' +
                    'Dismiss' +
                '</button>' +
            '</div>' +
            '<table style="width:100%; font-size:12px; ' +
            'margin-top:6px; border-collapse:collapse;">' +
                purgeRow("Scanned rows", r.scanned) +
                purgeRow("Unique kept", r.kept) +
                purgeRow("Duplicates found", r.attempted) +
                purgeRow("Server accepted", r.serverAccepted) +
                purgeRow("Verified deleted", r.verified,
                    r.verified > 0 ? "#0a5d0a" : "#7a1e1e") +
                purgeRow("Still present", r.stillPresent,
                    r.stillPresent > 0 ? "#7a1e1e" : "#0a5d0a") +
            '</table>' +
            delField + warn + errBlock;
        const dismiss = document.getElementById("purge-dismiss");
        if (dismiss) {
            dismiss.addEventListener("click", () => {
                chrome.storage.local.set({purgeStatus: null},
                    () => renderPurgeProgress());
            });
        }
        return;
    }

    // ── IN-PROGRESS: live status + optional progress bar ────────
    const pct = purgeStatus.total
        ? Math.round((purgeStatus.current / purgeStatus.total) * 100)
        : 0;
    let bar = "";
    if (purgeStatus.total && purgeStatus.phase === "deleting") {
        bar =
            '<div style="height:6px; background:#eee; ' +
            'border-radius:3px; margin-top:4px; overflow:hidden;">' +
            `<div style="width:${pct}%; height:100%; ` +
            'background:#6a2020;"></div></div>';
    }
    box.innerHTML =
        '<div style="font-weight:600; font-size:11px; ' +
        'text-transform:uppercase; color:#6a2020;">' +
        esc(purgeStatus.phase || "purge") +
        '</div>' +
        '<div style="font-size:12px; margin-top:2px;">' +
        esc(purgeStatus.message || "") +
        '</div>' +
        bar;
}

// Renders the edit-page HTML sample panel used when delete-button
// discovery failed for every record.  Includes a Copy-to-clipboard
// button so the user can paste the markup into a support ticket /
// chat without needing to open the service-worker devtools.
function renderEditHtmlSample(r) {
    const sampleId = "edit-html-sample-" + Date.now();
    // Note: the "click" wiring happens in setTimeout after this HTML
    // is injected — we can't attach listeners in the returned string.
    setTimeout(() => {
        const btn = document.getElementById(sampleId + "-copy");
        if (btn) {
            btn.addEventListener("click", async () => {
                try {
                    await navigator.clipboard.writeText(r.editHtmlSample);
                    btn.textContent = "Copied ✓";
                    setTimeout(() => {
                        btn.textContent = "Copy edit-page HTML";
                    }, 2000);
                } catch (e) {
                    btn.textContent = "Copy failed — select manually";
                }
            });
        }
    }, 50);
    return (
        '<div style="margin-top:8px; padding:6px 8px; ' +
        'background:#fff8e1; border:1px solid #ffe082; ' +
        'border-radius:3px; font-size:11px;">' +
        '<strong>Delete-button discovery failed.</strong> ' +
        'The edit page for ID ' + esc(String(r.editHtmlSampleId || "?")) +
        ' had no matching button pattern.  Copy the HTML sample below ' +
        'and share it so the regex can be updated:<br/>' +
        '<button id="' + sampleId + '-copy" style="margin-top:4px; ' +
        'padding:3px 8px; font-size:11px; cursor:pointer;">' +
        'Copy edit-page HTML</button>' +
        '<pre style="margin-top:4px; max-height:180px; ' +
        'overflow:auto; padding:6px; background:#fff; ' +
        'border:1px solid #ddd; border-radius:2px; font-size:10px; ' +
        'font-family:monospace; white-space:pre-wrap; ' +
        'word-break:break-all;">' +
        esc(r.editHtmlSample.substring(0, 3000)) +
        (r.editHtmlSample.length > 3000
            ? "\n\n… truncated at 3000 chars (full 5000 in clipboard)"
            : "") +
        '</pre></div>'
    );
}

// Small helper for the purge-complete results table row.
function purgeRow(label, value, color) {
    const c = color || "#222";
    return '<tr>' +
        '<td style="padding:2px 4px; color:#666;">' + esc(label) + '</td>' +
        '<td style="padding:2px 4px; text-align:right; ' +
        'font-weight:600; color:' + c + ';">' +
        esc(String(value ?? 0)) + '</td>' +
    '</tr>';
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
    await renderPurgeProgress();

    // Auto-refresh status + pending count every 3 seconds while
    // popup is open.  Purge progress refreshes on the same interval
    // AND immediately on storage change (below) for live 1-of-N updates.
    setInterval(async () => {
        await renderStatus();
        await refreshPendingCount();
        await renderPurgeProgress();
    }, 3000);

    // Instant-refresh purge progress the moment the background
    // service worker writes a new status — this is what makes the
    // "Deleting 47 of 170..." counter tick in real time.
    chrome.storage.onChanged.addListener((changes, area) => {
        if (area !== "local") return;
        if (changes.purgeStatus) {
            renderPurgeProgress();
        }
    });
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

// Inline two-click purge flow — no native confirm() dialogs.
// First click shows a yellow warning panel with a red "Confirm Purge"
// button + gray "Cancel".  Second click actually kicks off the purge.
// Results render in #purge-progress via renderPurgeProgress().
let _purgeArmed = false;
function armPurge() {
    _purgeArmed = true;
    const btn = $("purge-duplicates");
    btn.textContent = "⚠ Confirm Purge (click again)";
    btn.style.background = "#c33";
    btn.style.color = "#fff";
    btn.style.borderColor = "#a22";
    // Slide a cancel button in next to it.
    let cancel = document.getElementById("purge-cancel");
    if (!cancel) {
        cancel = document.createElement("button");
        cancel.id = "purge-cancel";
        cancel.textContent = "Cancel";
        cancel.style.marginTop = "4px";
        cancel.addEventListener("click", disarmPurge);
        btn.parentNode.appendChild(cancel);
    }
    // Also render an inline warning box explaining what will happen.
    const box = $("purge-progress");
    box.style.display = "block";
    box.innerHTML =
        '<div style="font-weight:600; font-size:11px; ' +
        'text-transform:uppercase; color:#7a1e1e;">' +
            'Confirm: purge duplicates' +
        '</div>' +
        '<div style="font-size:12px; margin-top:4px; line-height:1.4;">' +
        'This scans every charity record on elks.org for the ' +
        'current lodge year, groups rows with IDENTICAL date + ' +
        'program + counts, and deletes all but the first of each ' +
        'group.<br/><br/>' +
        'Records without duplicates will NOT be touched.  ' +
        '<strong>This cannot be undone.</strong>' +
        '</div>';
    // Auto-disarm after 10 seconds so a stale armed state doesn't
    // fire accidentally on next click.
    setTimeout(() => {
        if (_purgeArmed) disarmPurge();
    }, 10000);
}
function disarmPurge() {
    _purgeArmed = false;
    const btn = $("purge-duplicates");
    btn.textContent = "Purge Duplicates on Elks.org";
    btn.style.background = "#fff3cd";
    btn.style.color = "";
    btn.style.borderColor = "#ffc107";
    const cancel = document.getElementById("purge-cancel");
    if (cancel) cancel.remove();
    // Clear the warning box.
    chrome.storage.local.set({purgeStatus: null},
        () => renderPurgeProgress());
}

$("purge-duplicates").addEventListener("click", async () => {
    if (!_purgeArmed) {
        armPurge();
        return;
    }
    // Second click — actually run the purge.
    _purgeArmed = false;
    const cancel = document.getElementById("purge-cancel");
    if (cancel) cancel.remove();
    const btn = $("purge-duplicates");
    btn.textContent = "Purging…";
    btn.style.background = "#fff3cd";
    btn.style.color = "";
    btn.style.borderColor = "#ffc107";
    btn.disabled = true;
    // Clear any previous done-state result so the progress box
    // starts fresh.
    await new Promise((r) =>
        chrome.storage.local.set({purgeStatus: {
            phase: "scanning",
            message: "Starting…",
            updated: Date.now(),
        }}, () => r())
    );
    await renderPurgeProgress();
    chrome.runtime.sendMessage({type: "purge_duplicates"}, (resp) => {
        btn.textContent = "Purge Duplicates on Elks.org";
        btn.disabled = false;
        if (!resp) return;
        if (!resp.ok) {
            // Render the error inline in the progress box (no alert).
            chrome.storage.local.set({purgeStatus: {
                phase: "error",
                message: "Purge failed: " + (resp.error || "unknown"),
                updated: Date.now(),
            }}, () => renderPurgeProgress());
            return;
        }
        // Success path — background already wrote result to
        // purgeStatus.  Render it now for immediate feedback.
        renderPurgeProgress();
    });
});

// Sync (Push + Purge) — one-click reconciliation.  No confirm dialog
// (unlike Purge alone) since Sync is designed to be safe to run any
// time: pushes only what's queued, deletes only same-fingerprint
// duplicates.  Runs push→purge and renders combined results inline.
$("sync-now").addEventListener("click", async () => {
    const btn = $("sync-now");
    btn.textContent = "Syncing…";
    btn.disabled = true;
    await new Promise((r) =>
        chrome.storage.local.set({purgeStatus: {
            phase: "sync_push",
            message: "Starting sync…",
            updated: Date.now(),
        }}, () => r())
    );
    await renderPurgeProgress();
    chrome.runtime.sendMessage({type: "sync_now"}, (resp) => {
        btn.textContent = "Sync (Push + Purge)";
        btn.disabled = false;
        if (!resp) return;
        if (!resp.ok) {
            chrome.storage.local.set({purgeStatus: {
                phase: "error",
                message: "Sync failed: " + (resp.error || "unknown"),
                updated: Date.now(),
            }}, () => renderPurgeProgress());
            return;
        }
        // Background already wrote combined result to purgeStatus
        // with phase="sync_done"; render it now.
        renderPurgeProgress();
        // Also refresh the pending count in the header now that
        // successful pushes have drained the queue.
        refreshPendingCount();
    });
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
