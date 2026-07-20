# Elks.org Push — Chrome Extension

Companion to the `elkscharity` Odoo 19 module.  Pushes Odoo-validated
charity contributions to elks.org's Local Lodge Reporting form,
using the user's own Chrome session (bypasses elks.org's server-side
bot detection).

## How to install (developer mode)

1. Open Chrome → `chrome://extensions/`.
2. Toggle **Developer mode** on (top right).
3. Click **Load unpacked**.
4. Point at this folder: `elkscharity/extension/`.
5. The "Elks.org Push" icon shows in your toolbar.

## First-time setup

1. In Odoo: **Preferences → Elks.org Credentials → Regenerate API
   Key**.  Copy the key from the notification.
2. Click the extension icon → paste **Odoo URL** (e.g.
   `https://www.lewistonelks896.com`) and the **API Key**.
3. Check **Enabled**, click **Save Settings**.
4. Click **Test Odoo Key** — should show your name.
5. In another tab, log into <https://www.elks.org>.
6. Back in the popup, click **Test Elks.org Session** — should say
   "session is ACTIVE".
7. That's it.  The extension polls Odoo once a minute.  When
   contributions are validated in Odoo, they get pushed to elks.org
   automatically.

## When Chrome is closed

The extension only runs while Chrome is open with the extension
enabled.  Close Chrome for a week?  Pushes queue in Odoo, waiting.
Open Chrome, log into elks.org, next poll cycle they all go out.

## When elks.org session expires

Extension shows "no elks session" status.  Re-log-in at elks.org
and pushes resume.  You do NOT need to touch the extension.

## When Odoo API key is regenerated

Odoo will reject the extension's old key (401).  Extension shows
"bad api key" status.  Paste the new key into the popup, save,
resume.

## Files

| file            | role                                              |
|-----------------|---------------------------------------------------|
| `manifest.json` | Chrome extension manifest v3.                     |
| `background.js` | Service worker.  Polls Odoo, POSTs to elks.org.   |
| `popup.html`    | Toolbar-icon popup markup.                        |
| `popup.js`      | Popup UI logic.                                   |
| `icons/`        | PNG icons (16/48/128px).  Replace with lodge crest if desired. |

## API contract (extension ↔ Odoo)

All endpoints authenticate via `X-Elks-Api-Key` header.  All
respond with `Access-Control-Allow-Origin: *`.

- `GET  /elkscharity/ext/v1/whoami` — sanity check the API key.
- `GET  /elkscharity/ext/v1/pending` — up to 25 pending contributions
  with their payloads + the form URL.
- `POST /elkscharity/ext/v1/mark_pushed` — body `{contribution_id, confirmation}`.
- `POST /elkscharity/ext/v1/mark_failed` — body `{contribution_id, error, html_snippet}`.
