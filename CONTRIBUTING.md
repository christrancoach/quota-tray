# Contributing

Thanks for helping. Bug reports, fixes and new providers are all welcome.

## Set up and run the tests

Windows 11 and Python 3.12 are needed. The app uses Windows APIs: Credential Manager, the registry
and named mutexes.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

The Qt tests run on Qt's offscreen platform, so they don't open windows. `.\build.ps1` runs the
same tests before it builds the exes, and stops if any fail.

## Code layout

| Package | What's in it |
|---|---|
| `quota_core` | Providers, poller, shared state, alerts, history, forecast, keepalive, install, usage-report data. No UI |
| `quota_core/providers` | One module per provider: read the CLI's login, call its usage endpoint, parse the response into a `Reading` |
| `quota_ui` | Shared Qt pieces: cards, cats, report window, settings dialog |
| `quota_tray`, `quota_widget` | The two thin front ends |

## Provider endpoints are undocumented and they break

Every usage endpoint this app calls is a private API used by the vendor's own CLI or website. None
are documented or supported, and they change without notice. [ENDPOINTS.md](ENDPOINTS.md) lists
them from most to least fragile.

When something breaks:
- **Check it's really the API.** Look at `%APPDATA%\quota-tray\debug.log`: an unexpected response
  shape is logged there, with secrets redacted. The card also shows "Response changed: …".
- **Share responses only after scrubbing them.** Remove tokens, account IDs, emails and anything
  else personal, and never post a raw response you haven't read.
- **Update the fixtures.** Add or refresh a fixture in `tests/fixtures/` with the new shape (scrubbed
  the same way) and a test that parses it.
- **Keep the rules:**
  - **Read-only:** never write, refresh or rotate a CLI's credentials. If a token is expired,
    report it and skip.
  - **Own User-Agent:** send the app's own User-Agent (`quota-tray/…`). A change that presents the
    app as another client belongs behind an opt-in setting with a warning, like the existing
    "unofficial source" for Claude banked resets, and must be off by default.
  - **No quota spent:** never send a prompt or anything else that uses quota.
  - **No secrets in logs:** don't log tokens or secrets. `quota_core.redact` covers the log file;
    keep new code from printing them anywhere else.
  - **Polling floor:** keep the 5-minute minimum between polls per provider.

## Pull requests

- Keep changes focused and add a test for the behavior you changed.
- Match the surrounding style. The project has no formatter config; lines are up to about 120 characters.
- Use US spelling in code, UI text and docs.
- Update the README and ENDPOINTS.md if you change what the app sends or stores.
