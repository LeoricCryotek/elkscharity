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
// duplicate records on elks.org.  Groups records by their rendered
// row content (date + program + counts + $) and deletes all but the
// FIRST of each group.
//
// v1.3.0 rewrite:
//   * Live progress ("Deleting 47 of 170...") pushed to chrome.storage
//     so the popup can render it in real time.
//   * Delete flow: FETCH each row's edit page first, parse out the
//     real delete-button field name + any theUID CSRF token, then
//     POST with the exact fields elks.org expects.  Prior versions
//     guessed "deleteRecord=Delete This Program" and the server
//     silently ignored the POST (returned 200 but did nothing) —
//     hence "170 deleted" alerts with zero actual deletions.
//   * BULK VERIFY at the end: re-fetch the landing page and count
//     how many of our target IDs are actually gone.  The alert
//     reports VERIFIED deletions, not "server said 200".
//   * Per-record console log ("Purge 47/170: ID=NNN -> deleted") so
//     the Chrome console can be tailed while purge runs.
async function purgeDuplicates() {
    const formUrl =
        "https://www.elks.org/grandlodge/charity/local.cfm";

    async function setProgress(patch) {
        return new Promise((r) =>
            chrome.storage.local.set({purgeStatus: {
                ...(patch),
                updated: Date.now(),
            }}, () => r())
        );
    }

    await setProgress({phase: "scanning", message: "Loading landing page…"});
    console.log("[Elks.org Purge] Loading landing page…");

    const landingResp = await fetch(formUrl, {
        method: "GET",
        credentials: "include",
        redirect: "follow",
    });
    if (landingResp.status !== 200) {
        await setProgress({phase: "error",
            message: "Landing page HTTP " + landingResp.status});
        throw new Error("landing page HTTP " + landingResp.status);
    }
    const landingHtml = await landingResp.text();
    if (/elkslogin\.cfm/i.test(landingResp.url || "")) {
        await setProgress({phase: "error",
            message: "elks.org session expired — log in first"});
        throw new Error("elks.org session expired — log in first");
    }

    // Parse every <tr> containing an ID form input.  Regex-based
    // because DOMParser isn't available in MV3 service workers.
    const records = [];
    const trRegex = /<tr\b[^>]*>([\s\S]*?)<\/tr>/gi;
    let m;
    while ((m = trRegex.exec(landingHtml)) !== null) {
        const rowHtml = m[1];
        const idMatch = rowHtml.match(
            /name=["']ID["']\s+value=["'](\d+)["']/i
        );
        if (!idMatch) continue;
        const rid = idMatch[1];
        if (!rid || rid === "-1") continue;
        if (!/name=["']editRecord["']/i.test(rowHtml)) continue;
        const rowText = rowHtml
            .replace(/<script[\s\S]*?<\/script>/gi, "")
            .replace(/<style[\s\S]*?<\/style>/gi, "")
            .replace(/<[^>]+>/g, " ")
            .replace(/&nbsp;/gi, " ")
            .replace(/&amp;/gi, "&")
            .replace(/\s+/g, " ")
            .trim();
        records.push({ id: rid, rowText });
    }

    // Group by row-content fingerprint; keep first of each group.
    const groups = new Map();
    for (const rec of records) {
        const key = rec.rowText;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(rec);
    }
    const toDelete = [];
    let kept = 0;
    for (const [, group] of groups.entries()) {
        kept++;
        for (let i = 1; i < group.length; i++) toDelete.push(group[i]);
    }
    const scanned = records.length;
    const total = toDelete.length;

    console.log(
        `[Elks.org Purge] Scanned ${scanned} rows, ${kept} unique, ` +
        `${total} duplicates to delete`,
    );
    await setProgress({
        phase: "planning",
        message: `Found ${total} duplicates in ${scanned} rows`,
        current: 0,
        total,
    });

    if (total === 0) {
        await setProgress({
            phase: "done",
            message: "No duplicates found",
            current: 0,
            total: 0,
        });
        return { scanned, kept, deleted: 0, verified: 0, errors: [] };
    }

    let attempted = 0;
    let serverAccepted = 0;
    const errors = [];
    // Discover the correct delete-field name from the FIRST record's
    // edit page.  Cache it — every record uses the same edit template.
    let cachedDelFieldName = null;
    let cachedDelFieldValue = null;

    for (let i = 0; i < toDelete.length; i++) {
        const rec = toDelete[i];
        attempted++;
        const msg = `Deleting ${i + 1} of ${total} (ID ${rec.id})…`;
        await setProgress({
            phase: "deleting",
            message: msg,
            current: i + 1,
            total,
        });
        console.log(`[Elks.org Purge] ${msg}`);

        try {
            // Step 1: Fetch the edit page to discover the delete
            // button's real field name + any per-request theUID.
            const editBody = new URLSearchParams();
            editBody.set("ID", rec.id);
            editBody.set("editRecord", "Edit");
            const editResp = await fetch(formUrl, {
                method: "POST",
                credentials: "include",
                redirect: "follow",
                body: editBody.toString(),
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            });
            if (editResp.status !== 200) {
                errors.push(
                    `ID ${rec.id}: edit page HTTP ${editResp.status}`
                );
                continue;
            }
            const editHtml = await editResp.text();
            // Look for a submit button whose value contains "Delete".
            // Elks.org's form uses <input type="submit" name="X"
            // value="Delete This Program"> or similar wording.
            if (!cachedDelFieldName) {
                const delBtn = editHtml.match(
                    /<input[^>]*type=["']submit["'][^>]*name=["']([^"']+)["'][^>]*value=["']([^"']*[Dd]elete[^"']*)["']/i
                ) || editHtml.match(
                    /<input[^>]*name=["']([^"']+)["'][^>]*type=["']submit["'][^>]*value=["']([^"']*[Dd]elete[^"']*)["']/i
                ) || editHtml.match(
                    /<input[^>]*value=["']([^"']*[Dd]elete[^"']*)["'][^>]*name=["']([^"']+)["']/i
                );
                if (!delBtn) {
                    errors.push(
                        `ID ${rec.id}: no Delete button found on edit page ` +
                        `— dumping first 2000 chars of edit HTML to console`
                    );
                    console.warn(
                        `[Elks.org Purge] Edit page for ID=${rec.id} had ` +
                        `no Delete button.  HTML head:\n` +
                        editHtml.substring(0, 2000)
                    );
                    continue;
                }
                // Handle both regex orderings.
                if (/[Dd]elete/.test(delBtn[1])) {
                    cachedDelFieldValue = delBtn[1];
                    cachedDelFieldName = delBtn[2];
                } else {
                    cachedDelFieldName = delBtn[1];
                    cachedDelFieldValue = delBtn[2];
                }
                console.log(
                    `[Elks.org Purge] Discovered delete field: ` +
                    `name="${cachedDelFieldName}" ` +
                    `value="${cachedDelFieldValue}"`,
                );
                await setProgress({
                    phase: "deleting",
                    message: `${msg} (delete field: ${cachedDelFieldName})`,
                    current: i + 1,
                    total,
                });
            }
            // theUID token if elks.org includes one on the edit page.
            const uidMatch = editHtml.match(
                /name=["']theUID["']\s+value=["']([^"']*)["']/i
            );

            // Step 2: POST the actual delete.
            const delBody = new URLSearchParams();
            delBody.set("ID", rec.id);
            delBody.set(cachedDelFieldName, cachedDelFieldValue);
            if (uidMatch) delBody.set("theUID", uidMatch[1]);
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
                    `ID ${rec.id}: delete POST HTTP ${delResp.status}`
                );
            } else {
                serverAccepted++;
                console.log(
                    `[Elks.org Purge] ${i + 1}/${total}: ID=${rec.id} ` +
                    `-> POST accepted (verification pending)`,
                );
            }
            // Small delay to stay polite.
            await new Promise((r) => setTimeout(r, 250));
        } catch (e) {
            errors.push(`ID ${rec.id}: ${e.message}`);
            console.error(
                `[Elks.org Purge] ID=${rec.id} threw:`, e,
            );
        }
    }

    // Step 3: VERIFY by re-fetching the landing page and counting
    // how many of our target IDs are still present.
    await setProgress({
        phase: "verifying",
        message: `Verifying — re-fetching landing page…`,
        current: total,
        total,
    });
    console.log("[Elks.org Purge] Verifying by re-fetching landing…");

    const verifyResp = await fetch(formUrl, {
        method: "GET",
        credentials: "include",
        redirect: "follow",
    });
    if (verifyResp.status !== 200) {
        await setProgress({
            phase: "error",
            message: "Verify HTTP " + verifyResp.status,
        });
        throw new Error("verify landing HTTP " + verifyResp.status);
    }
    const verifyHtml = await verifyResp.text();
    let stillPresent = 0;
    for (const rec of toDelete) {
        const idPattern = new RegExp(
            `name=["']ID["']\\s+value=["']${rec.id}["']`
        );
        if (idPattern.test(verifyHtml)) stillPresent++;
    }
    const verified = total - stillPresent;

    if (stillPresent > 0 && verified === 0) {
        errors.push(
            `NOTHING was actually deleted despite ${serverAccepted} ` +
            `HTTP 200 responses.  The delete field name ` +
            `"${cachedDelFieldName}" may be wrong.  Check the console ` +
            `for the edit-page HTML dump above.`
        );
    } else if (stillPresent > 0) {
        errors.push(
            `${stillPresent} duplicate(s) still present after purge — ` +
            `may need to run purge again`
        );
    }

    await setProgress({
        phase: "done",
        message:
            `Done. Scanned ${scanned}, kept ${kept}, ` +
            `verified deletions ${verified}/${total}`,
        current: total,
        total,
        // Full result payload so the popup can render the summary
        // inline (no alert() dialog required).  Survives popup close.
        result: {
            scanned,
            kept,
            attempted,
            serverAccepted,
            verified,
            stillPresent,
            errors,
            delFieldName: cachedDelFieldName,
            delFieldValue: cachedDelFieldValue,
        },
    });
    console.log(
        `[Elks.org Purge] Final: verified ${verified}/${total} deleted, ` +
        `${stillPresent} still present, ${errors.length} errors`,
    );

    return {
        scanned,
        kept,
        attempted,
        serverAccepted,
        deleted: verified,
        stillPresent,
        errors,
    };
}

// ── main poll cycle ────────────────────────────────────────────────
// Returns a result object so callers (Sync button) can report totals
// inline instead of pulling them from lastStatus.  Shape:
//   {ok, phase, pushed, failed, attempted, skipped, message}
// where phase describes why we returned early (disabled/not_configured/
// no_elks_session/no_pending/completed).
async function pollAndPush() {
    const {enabled, odooUrl, apiKey, dryRun} = await getSettings();
    if (!enabled) {
        await setStatus({state: "disabled"});
        return {ok: true, phase: "disabled", pushed: 0, failed: 0,
                attempted: 0, skipped: 0,
                message: "Push disabled — no-op."};
    }
    if (!odooUrl || !apiKey) {
        await setStatus({
            state: "not_configured",
            message: "Set Odoo URL + API Key in the popup.",
        });
        return {ok: false, phase: "not_configured", pushed: 0,
                failed: 0, attempted: 0, skipped: 0,
                message: "Odoo URL + API Key not configured."};
    }

    // DRY RUN skips the elks.org session check entirely — the whole
    // point of dry run is to exercise the Odoo side without needing
    // an authenticated elks.org tab at all.
    if (!dryRun) {
        // Are we logged into elks.org?  Do this FIRST so we can tell
        // the user why nothing is being pushed.
        const sess = await elksSessionAlive();
        if (!sess.ok) {
            const emsg =
                "Log in at elks.org to enable pushes (session " +
                "check landed at " + sess.url + ").";
            await setStatus({
                state: "no_elks_session",
                message: emsg,
            });
            return {ok: false, phase: "no_elks_session", pushed: 0,
                    failed: 0, attempted: 0, skipped: 0, message: emsg};
        }
    }

    // Ask Odoo for pending.
    let pending;
    try {
        pending = await odooGet("/elkscharity/ext/v1/pending");
    } catch (e) {
        const emsg = "Couldn't reach Odoo: " + e.message;
        await setStatus({state: "odoo_error", message: emsg});
        return {ok: false, phase: "odoo_error", pushed: 0, failed: 0,
                attempted: 0, skipped: 0, message: emsg};
    }
    if (pending.status === 401) {
        const emsg =
            "Odoo rejected the API key.  Regenerate it in " +
            "Preferences → Elks.org Credentials and paste the " +
            "new value here.";
        await setStatus({state: "bad_api_key", message: emsg});
        return {ok: false, phase: "bad_api_key", pushed: 0, failed: 0,
                attempted: 0, skipped: 0, message: emsg};
    }
    if (pending.status !== 200 || !pending.json.ok) {
        const emsg =
            "Odoo returned HTTP " + pending.status + " — " +
            (pending.json.error || "unexpected response");
        await setStatus({state: "odoo_error", message: emsg});
        return {ok: false, phase: "odoo_error", pushed: 0, failed: 0,
                attempted: 0, skipped: 0, message: emsg};
    }

    const items = pending.json.items || [];
    const formUrl = pending.json.form_url;
    if (items.length === 0) {
        await setStatus({
            state: "idle",
            message: "No pending pushes.  Waiting.",
            lastCount: 0,
        });
        return {ok: true, phase: "no_pending", pushed: 0, failed: 0,
                attempted: 0, skipped: 0,
                message: "No pending contributions."};
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
    return {
        ok: failures === 0,
        phase: "completed",
        pushed: successes,
        failed: failures,
        attempted: items.length,
        skipped: 0,
        lastError,
        message:
            (dryRun ? "DRY RUN — " : "") +
            successes + " pushed, " + failures + " failed " +
            "(of " + items.length + ").",
    };
}

// ── SYNC (push + purge in one shot) ─────────────────────────────────
// One-click reconciliation between Odoo and elks.org.  Runs
// pollAndPush() then purgeDuplicates() in sequence, both reporting
// to chrome.storage.local.purgeStatus so the popup renders a single
// live status stream + a combined results table at the end.
//
// Sync flow rationale:
//   PUSH FIRST — so any missing records get added, THEN duplicates
//   (whether from this push or a prior retry storm) get cleaned.
//   Purge-first would leave a race window where a subsequent push
//   creates duplicates that stick around until the next sync.
async function syncNow(opts) {
    const forcePush = !!(opts && opts.forcePush);

    async function setSyncProgress(patch) {
        return new Promise((r) =>
            chrome.storage.local.set({purgeStatus: {
                ...patch,
                isSync: true,
                updated: Date.now(),
            }}, () => r())
        );
    }

    console.log("[Elks.org Sync] Starting push+purge sync");
    await setSyncProgress({
        phase: "sync_push",
        message: "Phase 1/2 — pushing pending contributions…",
    });

    // Force enable for the sync run if the user has push disabled.
    // Restore afterwards so the alarm cycle keeps their preference.
    let restoreEnabled = null;
    if (forcePush) {
        const s = await getSettings();
        if (!s.enabled) {
            restoreEnabled = false;
            await setSettings({enabled: true});
        }
    }

    let pushResult;
    try {
        pushResult = await pollAndPush();
    } catch (e) {
        pushResult = {
            ok: false,
            phase: "exception",
            pushed: 0,
            failed: 0,
            attempted: 0,
            message: "Push threw: " + e.message,
        };
        console.error("[Elks.org Sync] push phase threw:", e);
    } finally {
        if (restoreEnabled !== null) {
            await setSettings({enabled: restoreEnabled});
        }
    }

    console.log("[Elks.org Sync] Push phase result:", pushResult);
    await setSyncProgress({
        phase: "sync_between",
        message:
            "Phase 1 done (" + (pushResult.pushed || 0) +
            " pushed, " + (pushResult.failed || 0) + " failed). " +
            "Starting purge…",
    });
    // Brief pause so any just-created elks.org rows show up on the
    // landing page before the purge scans it.
    await new Promise((r) => setTimeout(r, 1500));

    // ── Phase 2: purge duplicates ─────────────────────────────
    // If push failed on session/config, purge will also fail — but
    // we still try so the user sees BOTH sets of errors, not just
    // whichever fired first.
    let purgeResult;
    try {
        purgeResult = await purgeDuplicates();
    } catch (e) {
        purgeResult = {
            scanned: 0, kept: 0, attempted: 0, serverAccepted: 0,
            deleted: 0, stillPresent: 0,
            errors: ["purge threw: " + e.message],
        };
        console.error("[Elks.org Sync] purge phase threw:", e);
    }
    console.log("[Elks.org Sync] Purge phase result:", purgeResult);

    // ── Combined result ──────────────────────────────────────
    const combined = {
        // Push side
        pushPhase: pushResult.phase,
        pushed: pushResult.pushed || 0,
        pushFailed: pushResult.failed || 0,
        pushAttempted: pushResult.attempted || 0,
        pushMessage: pushResult.message || "",
        pushLastError: pushResult.lastError || null,
        // Purge side
        scanned: purgeResult.scanned || 0,
        kept: purgeResult.kept || 0,
        duplicatesFound: purgeResult.attempted || 0,
        serverAccepted: purgeResult.serverAccepted || 0,
        verified: purgeResult.deleted || 0,
        stillPresent: purgeResult.stillPresent || 0,
        purgeErrors: purgeResult.errors || [],
    };

    await setSyncProgress({
        phase: "sync_done",
        message:
            "Sync complete. " +
            combined.pushed + " pushed, " +
            combined.verified + " duplicates removed.",
        current: 1, total: 1,
        result: combined,
    });
    console.log("[Elks.org Sync] Done:", combined);

    if (combined.pushed > 0 || combined.verified > 0) {
        try {
            chrome.notifications.create({
                type: "basic",
                iconUrl: chrome.runtime.getURL("icons/icon-128.png"),
                title: "Elks.org Sync",
                message:
                    combined.pushed + " pushed, " +
                    combined.verified + " duplicates removed.",
                priority: 0,
            });
        } catch (_) {}
    }

    return combined;
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
    if (msg && msg.type === "sync_now") {
        // forcePush temporarily flips enabled=true if the user has
        // push disabled so a manual sync always exercises push.
        // Restored to the user's preference before sync returns.
        syncNow({forcePush: true})
            .then((r) => sendResponse({ok: true, result: r}))
            .catch((e) => sendResponse({ok: false, error: e.message}));
        return true;
    }
});
