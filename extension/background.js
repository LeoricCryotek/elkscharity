// ============================================================================
// Elks.org Push — background service worker
// ============================================================================
// Runs in the extension's background context.  Its jobs:
//
//   1. Wake up on chrome.alarms every ~60 seconds.
//   2. Ask Odoo for pending pushes (GET /elkscharity/ext/v1/pending).
//   3. For each pending contribution, POST it directly to
//      https://www.elks.org/grandlodge/charity/local.cfm using the
//      user's active elks.org session cookies.  Chrome automatically
//      attaches the cookies because this fetch is same-origin from
//      the extension's point of view (host_permissions grants access
//      to www.elks.org).
//   4. Report success/failure back to Odoo.
//
// Manifest V3 note: the service worker sleeps after ~30s idle.  All
// state lives in chrome.storage.local, never in module-level vars.
// The alarm wakes us; we do the work; we go back to sleep.
//
// Everything is idempotent — if a submission was pushed but the
// mark_pushed callback failed, next poll will re-submit it.
// Downstream, elks.org itself dedupes on programDate + programID +
// programName so double-submits show up as a single record.  If you
// need stricter dedupe, the extension could remember which
// contribution IDs it has attempted in the current session, but
// that adds complexity for marginal benefit.
// ============================================================================

const ALARM_NAME = "elks_push_poll";
const POLL_MINUTES = 1;            // between polls
const WHOAMI_TIMEOUT = 10_000;     // whoami / mark_* HTTP timeout
const SUBMIT_TIMEOUT = 30_000;     // per-submission HTTP timeout
const ELKS_SESSION_URL =           // used to detect "logged in?"
    "https://www.elks.org/members/default.cfm";

// ── settings helpers ────────────────────────────────────────────────
async function getSettings() {
    return new Promise((res) => {
        chrome.storage.local.get(
            ["odooUrl", "apiKey", "enabled", "dryRun"],
            (v) => res(v || {}),
        );
    });
}
async function setSettings(patch) {
    return new Promise((res) => {
        chrome.storage.local.set(patch, () => res());
    });
}

// ── status tracking for the popup ──────────────────────────────────
async function setStatus(patch) {
    const cur = await new Promise((r) =>
        chrome.storage.local.get(["lastStatus"], (v) => r(v.lastStatus || {}))
    );
    const merged = {...cur, ...patch, updated: new Date().toISOString()};
    await new Promise((r) =>
        chrome.storage.local.set({lastStatus: merged}, () => r())
    );
}

// ── HTTP wrappers ──────────────────────────────────────────────────
async function odooGet(path) {
    const {odooUrl, apiKey} = await getSettings();
    if (!odooUrl || !apiKey) throw new Error("not_configured");
    const url = odooUrl.replace(/\/+$/, "") + path;
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), WHOAMI_TIMEOUT);
    try {
        const resp = await fetch(url, {
            method: "GET",
            headers: {"X-Elks-Api-Key": apiKey},
            signal: ctl.signal,
            // credentials: 'omit' — extension has no cookies for
            // Odoo domain, and we don't want to.  API key is the auth.
            credentials: "omit",
        });
        const text = await resp.text();
        let json = {};
        try { json = JSON.parse(text); } catch(_) {}
        return {status: resp.status, json, text};
    } finally { clearTimeout(to); }
}
async function odooPost(path, body) {
    const {odooUrl, apiKey} = await getSettings();
    if (!odooUrl || !apiKey) throw new Error("not_configured");
    const url = odooUrl.replace(/\/+$/, "") + path;
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), WHOAMI_TIMEOUT);
    try {
        const resp = await fetch(url, {
            method: "POST",
            headers: {
                "X-Elks-Api-Key": apiKey,
                "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
            signal: ctl.signal,
            credentials: "omit",
        });
        const text = await resp.text();
        let json = {};
        try { json = JSON.parse(text); } catch(_) {}
        return {status: resp.status, json, text};
    } finally { clearTimeout(to); }
}

// ── elks.org session detection ─────────────────────────────────────
// Test whether the browser has an active elks.org session by GETing
// a members-only page and inspecting the response.  If we land on
// /login or a redirect chain leading back to login, no session.
async function elksSessionAlive() {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), WHOAMI_TIMEOUT);
    try {
        const resp = await fetch(ELKS_SESSION_URL, {
            method: "GET",
            redirect: "follow",
            credentials: "include",
            signal: ctl.signal,
        });
        const finalUrl = resp.url || "";
        // Landed off the login page + 200 → good session.
        // Landed on elksLogin.cfm → session expired or not logged in.
        if (resp.status !== 200) return {ok: false, url: finalUrl};
        if (/login/i.test(finalUrl)) return {ok: false, url: finalUrl};
        return {ok: true, url: finalUrl};
    } catch (e) {
        return {ok: false, url: "(network error: " + e.message + ")"};
    } finally { clearTimeout(to); }
}

// Helper: pull an inline error message out of an elks.org response
// body.  Returns a short string (trimmed, single-line) or null.
// Elks.org uses ColdFusion which surfaces validation errors as
// JavaScript alert() calls or as <div class="alert">/<span class=
// "error"> banners.  Try DOMParser first; regex fallbacks after.
function extractElksErrorMessage(html) {
    // ColdFusion cfform errors are pushed to a JS array and
    // shown via alert().  Match the array-push lines directly.
    // Example: _CF_error_messages[0] = "Please submit the ...";
    // Ignore the "Final Charitable Report will be enabled April 1,
    // YYYY" text — that's the tooltip on a disabled year-end-only
    // button on the landing page, not an actual submission error.
    // Same reason we no longer key success detection off "Days
    // Since Last Charitable Event" alone.
    const NOISE_PATTERNS = [
        /final charitable report/i,
    ];
    const isNoise = (msg) => NOISE_PATTERNS.some((rx) => rx.test(msg));

    const cfMatches = [
        ...html.matchAll(
            /_CF_error_messages\[\d+\]\s*=\s*['"]([^'"]{5,300})['"]/g
        ),
    ].map((m) => m[1].trim()).filter((m) => !isNoise(m));
    if (cfMatches.length) {
        return cfMatches.slice(0, 3).join(" | ");
    }
    // Bootstrap alert banners.
    try {
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, "text/html");
        const banner = doc.querySelector(
            ".alert-danger, .alert-warning, div.error, span.error, " +
            ".Message[style*='red'], .errorMessage"
        );
        if (banner) {
            const t = (banner.textContent || "").trim().replace(/\s+/g, " ");
            if (t.length >= 5 && t.length <= 300) return t;
        }
        // Look for any <script>alert('...')</script> that CF might inject.
        for (const s of doc.querySelectorAll("script")) {
            const c = s.textContent || "";
            const m = c.match(/alert\s*\(\s*['"]([^'"]{5,300})['"]/);
            if (m) return m[1].trim();
        }
    } catch (_) {}
    // Regex fallback for the same alert pattern outside <script>.
    const alertMatch = html.match(
        /alert\s*\(\s*['"]([^'"]{5,300})['"]/
    );
    if (alertMatch) return alertMatch[1].trim();
    return null;
}

// Helper: extract the theUID token from an elks.org form HTML.
// Returns the token string or null.  DOMParser first, then regex
// fallbacks for weird attribute orderings.
function extractTheUID(formHtml) {
    let theUID = null;
    try {
        const parser = new DOMParser();
        const doc = parser.parseFromString(formHtml, "text/html");
        const uidInput = doc.querySelector('input[name="theUID"]');
        if (uidInput) {
            theUID = uidInput.getAttribute("value")
                  || uidInput.value
                  || null;
        }
    } catch (e) {
        console.warn("[Elks.org Push] DOMParser failed:", e);
    }
    if (!theUID) {
        const patterns = [
            /name=["']theUID["'][^>]*?value=["']([^"']+)["']/i,
            /value=["']([^"']+)["'][^>]*?name=["']theUID["']/i,
            /theUID['"]?\s*:\s*['"]([^'"]+)['"]/i,
            /theUID['"]?\s*=\s*['"]([^'"]+)['"]/i,
        ];
        for (const rx of patterns) {
            const m = formHtml.match(rx);
            if (m) { theUID = m[1]; break; }
        }
    }
    return theUID;
}

// ── one contribution submission ────────────────────────────────────
// elks.org's flow is TWO POSTs to /grandlodge/charity/local.cfm:
//   1. POST ID=-1&editRecord=Create+New+Charitable+Event
//      → server responds with the actual form HTML that includes
//        the theUID token (a per-request CSRF-style value)
//   2. POST all the field data + theUID + submitProgram
//      → server accepts + returns the "Days Since Last Charitable
//        Event" page with a 0 counter
//
// Previous versions did GET → find theUID → POST.  The GET landed
// on the LANDING page which only has a "Create New" button and no
// theUID field, so every submission failed.  Fixed in extension 1.2.6.
async function submitOne(formUrl, payload) {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), SUBMIT_TIMEOUT);
    try {
        // Step 1: trigger the "Create New Charitable Event" form
        // so the server hands us back the full form + theUID.
        const triggerBody = new URLSearchParams();
        triggerBody.set("ID", "-1");
        triggerBody.set("editRecord", "Create New Charitable Event");
        const formResp = await fetch(formUrl, {
            method: "POST",
            credentials: "include",
            redirect: "follow",
            body: triggerBody.toString(),
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
            },
            signal: ctl.signal,
        });
        if (formResp.status !== 200) {
            return {
                ok: false,
                error: "form trigger HTTP " + formResp.status,
                html: "",
            };
        }
        const formHtml = await formResp.text();
        if (/elkslogin\.cfm/i.test(formResp.url || "")) {
            return {
                ok: false,
                error: "elks.org session expired — please log in " +
                       "at https://www.elks.org and try again",
                html: formHtml.slice(0, 20000),
            };
        }
        const theUID = extractTheUID(formHtml);
        if (!theUID) {
            console.error(
                "[Elks.org Push] no theUID in " + formHtml.length +
                " chars of form HTML. First 500 chars:",
                formHtml.slice(0, 500),
            );
            return {
                ok: false,
                error: "couldn't find theUID token in " +
                       formHtml.length + " chars of form page " +
                       "(URL: " + (formResp.url || "?") + ") — the " +
                       "elks.org form structure may have changed.  " +
                       "Open the attached HTML file to check.",
                html: formHtml.slice(0, 20000),
            };
        }

        // Step 2: POST the form.  Build a URLSearchParams so the
        // Content-Type is application/x-www-form-urlencoded (what
        // the elks.org form expects).
        const pid = String(payload.programID || "9999");
        const other = payload.otherProgramID || "n/a";
        const form = new URLSearchParams();
        form.set("programDate",    payload.programDate || "");
        form.set("programID",      pid);
        form.set("otherProgramID", pid === "9999" ? other : "n/a");
        form.set("programName",    payload.programName || "");
        form.set("headcount",      String(payload.headcount || 0));
        form.set("numberElks",     String(payload.numberElks || 0));
        form.set("numberHelpers",  String(payload.numberHelpers || 0));
        form.set("hoursElks",      String(payload.hoursElks || 0));
        form.set("hoursHelpers",   String(payload.hoursHelpers || 0));
        form.set("milesElks",      String(payload.milesElks || 0));
        form.set("milesHelpers",   String(payload.milesHelpers || 0));
        form.set("nonCash",        String(payload.nonCash || 0));
        form.set("cash",           String(payload.cash || 0));
        form.set("recordID",       String(payload.recordID || -1));
        form.set("theUID",         theUID);
        form.set("submitProgram",  "Submit New Charitable Program");

        const postResp = await fetch(formUrl, {
            method: "POST",
            credentials: "include",
            redirect: "follow",
            body: form.toString(),
            headers: {
                "Content-Type":
                    "application/x-www-form-urlencoded",
            },
            signal: ctl.signal,
        });
        const body = await postResp.text();
        const low = body.toLowerCase();
        // Success signal (revised 1.2.8):  the ONLY reliable check
        // is whether elks.org returned the form or the landing page.
        //   - Form present → still on entry form → rejection
        //   - Form absent  → landing page → success
        // We detect the form by looking for the "submitProgram"
        // button name — that's the "Submit New Charitable Program"
        // input that only exists on the entry form.
        //
        // Prior versions also grepped for a "Final Charitable Report
        // will be enabled April 1, 2027" string to detect errors,
        // but that string ALSO appears on the landing page (as the
        // tooltip on a disabled "Submit Final Report" button), so
        // successful submissions got mis-classified as failures and
        // the extension retried, creating dozens of duplicates on
        // elks.org.
        const stillOnEntryForm = /name=["']submitProgram["']/i.test(body);
        if (!stillOnEntryForm) {
            // Landing page → save succeeded.  Try to extract the
            // most recent recordID from the response for the audit
            // trail; falls back to "OK" if we can't.
            const m = low.match(/recordid[^0-9]*(\d+)/);
            return {
                ok: true,
                confirmation: m ? "recordID=" + m[1] : "OK",
            };
        }
        // Entry form re-rendered → elks.org rejected our POST.
        // Pull whatever validation message we can.
        const elksMsg = extractElksErrorMessage(body);
        return {
            ok: false,
            error: elksMsg
                ? ("elks.org: " + elksMsg)
                : ("elks.org rejected the submission (form " +
                   "re-rendered with no extractable message — " +
                   "check attached HTML)"),
            html: body.slice(0, 20000),
        };
    } catch (e) {
        return {
            ok: false,
            error: "network error: " + e.message,
            html: "",
        };
    } finally { clearTimeout(to); }
}

// ── purge duplicates on elks.org ───────────────────────────────────
// One-shot cleanup for after a false-negative retry storm left many
// duplicate records on elks.org.  Fetches the landing page for the
// current lodge year, groups records by (programDate, programName,
// programID), and deletes all but the FIRST of each group.  Returns
// {scanned, kept, deleted, errors}.
//
// Elks.org's delete flow: POST to /grandlodge/charity/local.cfm with
// deleteRecord=Delete This Program + ID=<recordID>.  Confirmation
// dialog is client-side only; the server accepts a plain POST.
async function purgeDuplicates() {
    const formUrl =
        "https://www.elks.org/grandlodge/charity/local.cfm";
    const landingResp = await fetch(formUrl, {
        method: "GET",
        credentials: "include",
        redirect: "follow",
    });
    if (landingResp.status !== 200) {
        throw new Error("landing page HTTP " + landingResp.status);
    }
    const landingHtml = await landingResp.text();
    if (/elkslogin\.cfm/i.test(landingResp.url || "")) {
        throw new Error("elks.org session expired — log in first");
    }

    // Parse the landing page.  Each row is inside a form that
    // POSTs to /grandlodge/charity/local.cfm with a hidden ID
    // input and editRecord submit button.  Group siblings.
    const parser = new DOMParser();
    const doc = parser.parseFromString(landingHtml, "text/html");

    // Every existing record renders an <input name="ID" value="N">
    // followed by an <input name="editRecord"> in the same form.
    // Pull the IDs + surrounding row context for grouping.
    const records = [];
    for (const form of doc.querySelectorAll("form")) {
        const idInput = form.querySelector('input[name="ID"]');
        const editInput = form.querySelector('input[name="editRecord"]');
        if (!idInput || !editInput) continue;
        const rid = idInput.value;
        if (!rid || rid === "-1") continue;
        // Pull the row's text so we can group by
        // (programDate + programName) for dedupe grouping.
        // The parent <tr> has the date/name cells.
        let rowText = "";
        let node = form.parentElement;
        while (node && node.tagName !== "TR") node = node.parentElement;
        if (node) rowText = (node.textContent || "").trim().replace(/\s+/g, " ");
        records.push({ id: rid, rowText });
    }

    // Group by rowText (date + program name + counts — a full-row
    // fingerprint that will match exact duplicates from the false-
    // negative retry storm).
    const groups = new Map();
    for (const rec of records) {
        const key = rec.rowText;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(rec);
    }

    let scanned = records.length;
    let kept = 0;
    let deleted = 0;
    const errors = [];

    for (const [key, group] of groups.entries()) {
        // Keep the FIRST (usually the earliest/original submission);
        // delete the rest.
        kept++;
        for (let i = 1; i < group.length; i++) {
            const rec = group[i];
            try {
                const delBody = new URLSearchParams();
                delBody.set("ID", rec.id);
                delBody.set("deleteRecord", "Delete This Program");
                const delResp = await fetch(formUrl, {
                    method: "POST",
                    credentials: "include",
                    redirect: "follow",
                    body: delBody.toString(),
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                });
                if (delResp.status !== 200) {
                    errors.push(
                        `ID ${rec.id}: HTTP ${delResp.status}`
                    );
                } else {
                    deleted++;
                    console.log(
                        `[Elks.org Purge] 🗑 deleted duplicate ID=${rec.id}`,
                    );
                }
                await new Promise((r) => setTimeout(r, 300));
            } catch (e) {
                errors.push(`ID ${rec.id}: ${e.message}`);
            }
        }
    }

    return { scanned, kept, deleted, errors };
}

// ── main poll cycle ────────────────────────────────────────────────
async function pollAndPush() {
    const {enabled, odooUrl, apiKey, dryRun} = await getSettings();
    if (!enabled) {
        await setStatus({state: "disabled"});
        return;
    }
    if (!odooUrl || !apiKey) {
        await setStatus({
            state: "not_configured",
            message: "Set Odoo URL + API Key in the popup.",
        });
        return;
    }

    // DRY RUN skips the elks.org session check entirely — the whole
    // point of dry run is to exercise the Odoo side without needing
    // an authenticated elks.org tab at all.
    if (!dryRun) {
        // Are we logged into elks.org?  Do this FIRST so we can tell
        // the user why nothing is being pushed.
        const sess = await elksSessionAlive();
        if (!sess.ok) {
            await setStatus({
                state: "no_elks_session",
                message:
                    "Log in at elks.org to enable pushes (session " +
                    "check landed at " + sess.url + ").",
            });
            return;
        }
    }

    // Ask Odoo for pending.
    let pending;
    try {
        pending = await odooGet("/elkscharity/ext/v1/pending");
    } catch (e) {
        await setStatus({
            state: "odoo_error",
            message: "Couldn't reach Odoo: " + e.message,
        });
        return;
    }
    if (pending.status === 401) {
        await setStatus({
            state: "bad_api_key",
            message:
                "Odoo rejected the API key.  Regenerate it in " +
                "Preferences → Elks.org Credentials and paste the " +
                "new value here.",
        });
        return;
    }
    if (pending.status !== 200 || !pending.json.ok) {
        await setStatus({
            state: "odoo_error",
            message:
                "Odoo returned HTTP " + pending.status + " — " +
                (pending.json.error || "unexpected response"),
        });
        return;
    }

    const items = pending.json.items || [];
    const formUrl = pending.json.form_url;
    if (items.length === 0) {
        await setStatus({
            state: "idle",
            message: "No pending pushes.  Waiting.",
            lastCount: 0,
        });
        return;
    }
    await setStatus({
        state: "pushing",
        message: "Submitting " + items.length + " contribution(s)…",
    });

    let successes = 0, failures = 0;
    // Track the most recent error so the popup can show it instead of
    // just "N failed" with no context.  Cleared on each cycle.
    let lastError = null;
    for (const it of items) {
        const label = `#${it.id} ${it.display_name || ""}`;
        try {
            if (dryRun) {
                // Print the exact payload to devtools so QA can inspect.
                // Do NOT call mark_pushed — Dry Run must leave the
                // record in the queue so a real push can happen later.
                console.log(
                    `[Elks.org Push · DRY RUN] would submit ${label}:`,
                    JSON.stringify(it.payload, null, 2),
                );
                successes++;
                await new Promise((r) => setTimeout(r, 100));
                continue;
            }
            const result = await submitOne(formUrl, it.payload);
            if (result.ok) {
                console.log(
                    `[Elks.org Push] ✅ ${label} → ${result.confirmation}`,
                );
                await odooPost("/elkscharity/ext/v1/mark_pushed", {
                    contribution_id: it.id,
                    confirmation: result.confirmation,
                });
                successes++;
            } else {
                console.error(
                    `[Elks.org Push] ❌ ${label} — ${result.error}`,
                    { payload: it.payload, htmlSnippet: (result.html || "").slice(0, 500) },
                );
                lastError = { id: it.id, name: it.display_name, message: result.error };
                await odooPost("/elkscharity/ext/v1/mark_failed", {
                    contribution_id: it.id,
                    error: result.error,
                    html_snippet: result.html || "",
                });
                failures++;
            }
        } catch (e) {
            console.error(
                `[Elks.org Push] 💥 extension exception on ${label}:`, e,
            );
            lastError = { id: it.id, name: it.display_name, message: "extension exception: " + e.message };
            try {
                await odooPost("/elkscharity/ext/v1/mark_failed", {
                    contribution_id: it.id,
                    error: "extension exception: " + e.message,
                    html_snippet: "",
                });
            } catch (_) {}
            failures++;
        }
        // Gentle pacing — same 500ms as the Python client.
        await new Promise((r) => setTimeout(r, 500));
    }

    await setStatus({
        state: failures === 0 ? "idle" : "partial",
        message:
            (dryRun ? "DRY RUN — " : "") +
            (successes ? successes + " pushed" : "") +
            (successes && failures ? ", " : "") +
            (failures ? failures + " failed" : "") +
            (successes || failures ? "." : "") +
            (failures && lastError
                ? " Last error: " + (lastError.message || "").slice(0, 140)
                : "") +
            " Next poll in " + POLL_MINUTES + "m.",
        lastCount: items.length,
        lastSuccess: successes,
        lastFailure: failures,
        lastError: lastError,
    });

    if (successes > 0) {
        try {
            chrome.notifications.create({
                type: "basic",
                iconUrl: chrome.runtime.getURL("icons/icon-128.png"),
                title: "Elks.org Push",
                message: successes + " contribution(s) submitted" +
                         (failures ? ", " + failures + " failed." : "."),
                priority: 0,
            });
        } catch (_) {}
    }
}

// ── alarm wiring ────────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create(ALARM_NAME, {periodInMinutes: POLL_MINUTES});
    setSettings({enabled: false});  // opt-in
});
chrome.runtime.onStartup.addListener(() => {
    chrome.alarms.create(ALARM_NAME, {periodInMinutes: POLL_MINUTES});
});
chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_NAME) {
        pollAndPush().catch((e) => {
            console.error("elks push cycle failed:", e);
            setStatus({
                state: "error",
                message: "Cycle exception: " + e.message,
            });
        });
    }
});

// ── popup ↔ background message bridge ──────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg && msg.type === "run_now") {
        pollAndPush()
            .then(() => sendResponse({ok: true}))
            .catch((e) => sendResponse({ok: false, error: e.message}));
        return true;  // async
    }
    if (msg && msg.type === "test_whoami") {
        odooGet("/elkscharity/ext/v1/whoami")
            .then((r) => sendResponse({ok: true, result: r}))
            .catch((e) => sendResponse({ok: false, error: e.message}));
        return true;
    }
    if (msg && msg.type === "test_elks_session") {
        elksSessionAlive()
            .then((r) => sendResponse({ok: true, result: r}))
            .catch((e) => sendResponse({ok: false, error: e.message}));
        return true;
    }
    if (msg && msg.type === "purge_duplicates") {
        purgeDuplicates()
            .then((r) => sendResponse({ok: true, result: r}))
            .catch((e) => sendResponse({ok: false, error: e.message}));
        return true;
    }
});
