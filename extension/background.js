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
            ["odooUrl", "apiKey", "enabled"],
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

// ── one contribution submission ────────────────────────────────────
// GET the form (scrape theUID), then POST the payload.  Same
// two-step dance the Python client does.
async function submitOne(formUrl, payload) {
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), SUBMIT_TIMEOUT);
    try {
        // Step 1: fetch the form page to get a fresh theUID token.
        const formResp = await fetch(formUrl, {
            method: "GET",
            credentials: "include",
            redirect: "follow",
            signal: ctl.signal,
        });
        if (formResp.status !== 200) {
            return {
                ok: false,
                error: "form fetch HTTP " + formResp.status,
                html: "",
            };
        }
        const formHtml = await formResp.text();
        // If elks.org redirected us to login, no session.
        if (/elkslogin\.cfm/i.test(formResp.url || "")) {
            return {
                ok: false,
                error: "elks.org session expired — please log in " +
                       "at https://www.elks.org and try again",
                html: formHtml.slice(0, 4000),
            };
        }
        // Extract theUID.  Same regex pattern as the Python client.
        let uidMatch =
            formHtml.match(/name=["']theUID["']\s+value=["']([^"']+)["']/i)
         || formHtml.match(/value=["']([^"']+)["']\s+name=["']theUID["']/i);
        if (!uidMatch) {
            return {
                ok: false,
                error: "couldn't find theUID token on form page " +
                       "— session may not be authenticated",
                html: formHtml.slice(0, 4000),
            };
        }
        const theUID = uidMatch[1];

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
        // Success signals from the Python client, ported.
        if (low.includes("days since last charitable event") &&
            !low.includes('name="programid"')) {
            const m = low.match(/recordid[^0-9]*(\d+)/);
            return {
                ok: true,
                confirmation: m ? "recordID=" + m[1] : "OK",
            };
        }
        if (low.includes("error") &&
            (low.includes("required") || low.includes("please"))) {
            return {
                ok: false,
                error: "elks.org rejected the submission — see " +
                       "attached HTML for details",
                html: body.slice(0, 8000),
            };
        }
        // Ambiguous but 200 — treat as success but flag it.
        return {
            ok: true,
            confirmation: "OK (unverified — please spot-check on elks.org)",
        };
    } catch (e) {
        return {
            ok: false,
            error: "network error: " + e.message,
            html: "",
        };
    } finally { clearTimeout(to); }
}

// ── main poll cycle ────────────────────────────────────────────────
async function pollAndPush() {
    const {enabled, odooUrl, apiKey} = await getSettings();
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

    // Are we logged into elks.org?  Do this FIRST so we can tell the
    // user why nothing is being pushed.
    const sess = await elksSessionAlive();
    if (!sess.ok) {
        await setStatus({
            state: "no_elks_session",
            message:
                "Log in at elks.org to enable pushes (session check " +
                "landed at " + sess.url + ").",
        });
        return;
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
    for (const it of items) {
        try {
            const result = await submitOne(formUrl, it.payload);
            if (result.ok) {
                await odooPost("/elkscharity/ext/v1/mark_pushed", {
                    contribution_id: it.id,
                    confirmation: result.confirmation,
                });
                successes++;
            } else {
                await odooPost("/elkscharity/ext/v1/mark_failed", {
                    contribution_id: it.id,
                    error: result.error,
                    html_snippet: result.html || "",
                });
                failures++;
            }
        } catch (e) {
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
            (successes ? successes + " pushed" : "") +
            (successes && failures ? ", " : "") +
            (failures ? failures + " failed" : "") +
            (successes || failures ? "." : "") +
            " Next poll in " + POLL_MINUTES + "m.",
        lastCount: items.length,
        lastSuccess: successes,
        lastFailure: failures,
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
});
