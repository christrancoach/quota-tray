# Endpoints: what's undocumented and what will break first

**None of the usage endpoints is documented or supported for third-party use.** They're the
private APIs each vendor's own CLI or website uses. Ranked from most to least likely to break:

| Rank | Provider | Endpoint | Why it's fragile | Symptom when it breaks |
|---|---|---|---|---|
| 1 | Grok | `GET cli-chat-proxy.grok.com/v1/billing?format=credits` (+ `/v1/user?include=subscription` for the plan name) | A private proxy for the Grok CLI, with proto3 JSON (zero values are omitted). `productUsage` currently lists only GrokBuild, and xAI's statement that the weekly pool is shared across products isn't confirmed by this API. | ShapeError in debug.log, or a percentage that disagrees with grok.com |
| 2 | ChatGPT (Codex) | `GET chatgpt.com/backend-api/wham/usage` with `ChatGPT-Account-Id` | Internal ChatGPT backend. The 5-hour window has already been removed once, and window slots have moved between `primary_window` and `secondary_window`. The app classifies windows by `limit_window_seconds`, so slot moves don't matter. | Falls back to the Codex session logs automatically (source label *Codex session log*) |
| 3 | Claude | `GET api.anthropic.com/api/oauth/usage` with `anthropic-beta: oauth-2025-04-20` | Used by Claude Code's `/usage`, so it's stable in practice, but the response keeps gaining codename keys. The app reads `five_hour`, `seven_day`, `limits[]` and `seven_day_breakdown`, and ignores the rest. | ShapeError in debug.log if `seven_day` and `limits[].weekly_all` both disappear |

### Banked-reset calls (`reset_info` in config.json: on by default for Codex, **opt-in** for Claude)

| Provider | Endpoint | Notes | Symptom when it breaks |
|---|---|---|---|
| Claude (opt-in) | `GET api.anthropic.com/api/oauth/usage?at_wall=1&skip_spend=1` | Claude Code's own call. Same token and beta header as the usage call, but it only answers a `User-Agent` of the form `claude-cli/<version> (external, cli)`. Any other client gets `ineligible: surface`. Banked grants are in `cedar_ember` and the weekly session reset in `juniper_tide`, both codenames that could be renamed at any time. | The reset chip just disappears; the usage card is unaffected (best effort, logged only) |
| ChatGPT (Codex) | `GET chatgpt.com/backend-api/wham/rate-limit-reset-credits` with `ChatGPT-Account-Id` | The Codex CLI's own call, made with the app's own User-Agent. Lists reset credits with an expiry. `applicable_available_count` is the number usable right now; the rest become usable once you hit a limit. | The reset chip disappears; usage is unaffected |

Each provider also checks its usage response for the fields it relies on. When they go missing the
card shows "Response changed: …" and you get one notification, instead of a silently wrong number.

Credential formats are just as unofficial: the CLIs can change file layouts or move a token
somewhere else in any release. If a card suddenly says "No … login found" right after a CLI
update, check the file shown in the README table.

## Gemini: not used, and what each route would cost you

quota-tray makes no Gemini quota call. These routes are separate from the app's calls above.
Ranked from most to least likely to break, as a judgment based on the sources in the
[Gemini quota guide](docs/gemini-quota.md):

Rank is fragility order, not the route numbers used in the Gemini quota guide.

| Rank | Route | Endpoint | Why it's fragile | What it would show |
|---|---|---|---|---|
| 1 | Antigravity CLI (`agy`) | `POST cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`, described by third-party tools | No documented API. Tokens are kept in the OS keyring. The private call and a report of a base endpoint always showing 100% are not confirmed. | Per-model remaining fraction and reset time, according to third-party reports |
| 2 | Gemini CLI (Code Assist Standard/Enterprise login) | `POST cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota`, with a project ID | The version is literally `v1internal`. Login defaults to `~/.gemini/oauth_creds.json`, unless encrypted storage is enabled. Refreshing needs the CLI's own OAuth client. Free and AI Pro/Ultra logins no longer work in Gemini CLI. Source only; no live call tested. | Per-model `remainingFraction`, `resetTime`, and optional `remainingAmount` |
| 3 | Google Cloud project (Gemini API or Vertex AI) | Cloud Quotas API for limits; Cloud Monitoring `serviceruntime.googleapis.com/quota/*` for usage | Documented APIs. Needs a project ID and credentials with access to its quotas and metrics. Exact Gemini API metric names are not confirmed. | Project usage against limits, not a person's allowance |

An AI Studio API key uses project limits too. AI Studio shows the limits; we found no documented
remaining-quota API. The Gemini app has no documented
usage API; read Settings, then Usage Limits. Antigravity CLI provides an interactive `/usage` panel.
See the guide for sources and caveats. Treat any failure as unknown, never zero left.

## Usage report: the price list download

The usage report makes one outbound request, a read-only download of LiteLLM's public price list:
`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`.
It happens at most once a week and sends nothing about you or your usage. If the file moves or
changes format, the report keeps using the last cached copy (`litellm_prices.json`) and your
`pricing.json` overrides. New models only get a cost once they appear in the list or in
`pricing.json`; until then their tokens are counted and marked "(no price)".

## Update check

The poller fetches `update_url` once a day, by default the project's GitHub "latest release" API
(`api.github.com/repos/christrancoach/quota-tray/releases/latest`), sending only a
`quota-tray/<version>` User-Agent. Set it to empty in Settings to turn the check off.
It never downloads or runs an update itself.

## Identity

Apart from the opt-in Claude banked-reset call above, every request uses the CLI's own token, against an
endpoint that CLI itself calls, with the app's own User-Agent (`quota-tray/…`). The Grok calls also
send `x-grok-client-mode: cli`, the request mode the Grok CLI uses for this proxy; it names a
mode, not a client, and the User-Agent stays the app's own.
