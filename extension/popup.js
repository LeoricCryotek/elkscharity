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
            ["odooUrl", "apiKey", "enabled", "lastStatus"],
            (v) => res(v || {}),
        )
    );
}

async function renderStatus() {
    const {lastStatus} = await loadSettings();
    const box = $("status");
    box.className = "";
    if (!lastStatus) {
        box.innerHTML =
            '<div class="status-state">Idle</div>' +
            '<div class="muted">No poll cycles run yet.</div>';
        return;
    }
    box.className = lastStatus.state || "";
    box.innerHTML =
        '<div class="status-state">' +
            (lastStatus.state || "unknown").replace(/_/g, " ") +
        '</div>' +
        '<div>' + (lastStatus.message || "") + '</div>' +
        (lastStatus.updated
            ? '<div class="muted" style="margin-top:4px;">Updated ' +
              new Date(lastStatus.updated).toLocaleTimeString() +
              '</div>'
            : "");
}

async function init() {
    const s = await loadSettings();
    $("odoo-url").value = s.odooUrl || "";
    $("api-key").value  = s.apiKey  || "";
    $("enabled").checked = !!s.enabled;
    await renderStatus();

    // Auto-refresh status every 3 seconds while popup is open.
    setInterval(renderStatus, 3000);
}

$("save").addEventListener("click", async () => {
    const odooUrl = $("odoo-url").value.trim();
    const apiKey = $("api-key").value.trim();
    const enabled = $("enabled").checked;
    if (odooUrl && !/^https?:\/\//i.test(odooUrl)) {
        alert("Odoo URL must start with http:// or https://");
        return;
    }
    chrome.storage.local.set(
        {odooUrl, apiKey, enabled},
        () => {
            $("save").textContent = "Saved ✓";
            setTimeout(() => {
                $("save").textContent = "Save Settings";
            }, 1500);
        },
    );
});

$("run-now").addEventListener("click", async () => {
    $("run-now").textContent = "Running…";
    chrome.runtime.sendMessage({type: "run_now"}, (resp) => {
        $("run-now").textContent = "Push Now";
        setTimeout(renderStatus, 500);
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
