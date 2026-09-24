# Quota Tray v1.2.0

The first public release. Quota Tray shows how much of your Claude, ChatGPT Codex and Grok
subscription quota is left, from the Windows tray or a desktop widget. It uses the logins the
official CLIs already keep on your PC.

## Download

| File | What it is |
|---|---|
| `QuotaTray-1.2.0-win-x64.zip` | Both apps plus license files. **Start here** |
| `QuotaTray.exe` | Tray icon app |
| `QuotaWidget.exe` | Desktop widget app |
| `SHA256SUMS.txt` | SHA-256 checksums of the three files above |
| Source archives for the LGPL libraries (`qtbase`, `qtsvg`, `qtimageformats` 6.11.2, `pyside-setup` 6.11.2, pystray 0.19.5) and `SOURCES-SHA256.txt` | Attached to this release to meet the LGPL-3.0 source requirement for the Qt, PySide6 and pystray code bundled in the exes |

The exes aren't code-signed, so Windows SmartScreen will warn the first time you run them. Click
**More info → Run anyway**, or build from source; both are covered in the README. To check a download:
`Get-FileHash .\QuotaTray.exe -Algorithm SHA256` and compare the result with `SHA256SUMS.txt`.

## Highlights

- **Tray and widget:** a tray icon colored by your lowest weekly % left, and a see-through desktop
  widget with compact, card and **cats** views. In cats view each provider is a cat whose energy
  follows your quota.
- **Claude, ChatGPT (Codex) and Grok:** weekly and 5-hour bars, per-model limits and reset
  countdowns, each read the same way the provider's own CLI reads it.
- **Banked resets:** shows spare limit resets (Codex by default, and Claude if you opt in) on
  cards, compact rows and cats.
- **Forecast:** "runs out Thu 14:00, before the reset" or "on pace to finish with ~40% left", from
  your own reading history.
- **Alerts:** weekly and 5-hour thresholds, resets soon, reset back, and a banked-reset hint. Quiet
  hours hold alerts until they end.
- **Usage report:** estimated API-price cost from Claude Code, Codex, Grok and Gemini CLI session
  logs. It prices 1-hour cache writes, long-context tiers and fast mode.
- **Keepalive:** lets Claude Code and Grok CLI renew their own logins (`claude doctor`,
  `grok models`), so cards don't go stale. It sends no prompts and uses no quota.
- **Settings window with live reload**, keyboard control and screen reader support for the widget,
  and an optional per-user install with an Apps & features uninstall entry.
- **Update notices** from this repository's GitHub Releases. The app only tells you about a new
  version; it never downloads anything itself.

## Off by default: one unofficial source

**Claude banked resets via Claude Code client (unofficial)** presents the app as Claude Code for one
read-only request. It's off unless you turn it on in **Settings → Unofficial sources**, which shows a
warning first. It may violate Anthropic's terms and can stop working at any time.

## Not supported: Gemini

Google offers no supported way to read Gemini quota, so Gemini isn't shown. The usage report still
counts Gemini CLI tokens from its local logs.

## Known limitations

- Every usage endpoint is an undocumented private API and can change without notice. The app flags
  responses that change shape instead of showing wrong numbers.
- Tested on Windows 11 only.
- Not affiliated with or endorsed by Anthropic, OpenAI, xAI or Google.

## Licenses

MIT. The exes bundle LGPL-3.0 Qt/PySide6 and pystray; see `THIRD_PARTY_NOTICES.md` in the zip or
the repository.
