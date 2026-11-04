# Gemini quota: which route fits you

quota-tray does not call any of these Gemini quota routes. It has no Gemini quota card.
The usage report counts Gemini CLI tokens from local logs, which is separate from quota.

On **2026-06-18**, free Code Assist for individuals and Google AI Pro/Ultra logins moved from
Gemini CLI to Antigravity CLI. Gemini CLI keeps access for Code Assist Standard/Enterprise
licenses and paid API keys (sources S1 and S2 below).

| You use | Route | What you can read |
|---|---|---|
| Gemini CLI with a Code Assist Standard/Enterprise login | 1: private Code Assist endpoint | Per-model quota fields used by the CLI; source only, not tested live |
| A Gemini API key from AI Studio | 2: API limits | Project limits in AI Studio; no documented remaining-quota API found |
| A Google Cloud project for the Gemini API or Vertex AI | 3: Cloud Quotas and Cloud Monitoring | Project limits and usage, with the right access and available metrics |
| The Gemini app | 4: app screen | Settings, then Usage Limits; no documented API found |
| Antigravity CLI (`agy`) | 4: CLI screen | `/usage`, including Model Quotas; no documented API found |

## Route 1: Gemini CLI Code Assist login

The CLI uses the private `cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota` endpoint
with a project ID. Its source defines `modelId`, `remainingFraction`, `resetTime`, and optional
`remainingAmount`. This was read in source, not tested with a live account (S8, S9).
The login normally lives in `~/.gemini/oauth_creds.json`; encrypted storage can replace it (S10).
Refreshing the token needs the CLI's own OAuth client, which is defined in the CLI's source (S11).
For an interactive reading, the CLI offers `/stats model` (S12).

## Route 2: Gemini API key

API limits apply per project, not per key. Daily quotas reset at midnight Pacific time.
Read your per-model limits in AI Studio. A 429 error can signal a limit was reached (S13).
We found no documented API for quota remaining.
Your own request counts cannot account for other users of the project. For project metrics,
use Route 3 if you have access.

## Route 3: Google Cloud project

Cloud Quotas provides limits; Cloud Monitoring provides quota usage metrics (S6, S7).
You need a project ID and a Google Cloud login or service account with access to both.
Quota Viewer (`roles/servicemanagement.quotaViewer`) grants access to quotas (S6).
Monitoring needs its own read access. Monitoring lists
`serviceruntime.googleapis.com/quota/allocation/usage`, `quota/rate/net_usage`, `quota/limit`,
and `quota/exceeded` on the `consumer_quota` resource. Check which metrics your service exposes;
exact Gemini API metric names are not confirmed. These numbers cover a project, not a personal
Gemini subscription.

## Route 4: Gemini app and Antigravity CLI

In the Gemini app, open Settings, then Usage Limits (S14). In Antigravity CLI, open `/usage`
for Model Quotas (S3). We found no documented API for either screen.
Antigravity CLI moves sign-in tokens into the OS keyring (S4).
Third-party tools describe `cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels`
with per-model `quotaInfo.remainingFraction` and `resetTime` (S5). That private call is
not confirmed. A report that a base endpoint always returns 100% comes from a secondary summary
that could not be confirmed. Do not rely on it as a quota reading.

## Private endpoints and token safety

Undocumented endpoints can change or disappear without notice. Reusing a CLI login outside
the CLI may raise terms-of-service questions; we found no clear rule either way.
Never share or upload token files, or paste tokens into logs, chats, or bug reports.
**Treat any failure as unknown, never zero left.** No live quota calls were made for this guide.
See [ENDPOINTS.md](../ENDPOINTS.md#gemini-not-used-and-what-each-route-would-cost-you) for fragility.

## Sources

Sources, read 2026-11-04. Gemini CLI source links are pinned
to commit `89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31`. They describe source behavior, not a live test.

- **S1:** [Google's Gemini CLI transition announcement](https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/).
- **S2:** [gemini-cli README.md](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/README.md).
- **S3:** [Antigravity CLI usage command](https://antigravity.google/docs/cli/commands/usage).
- **S4:** [Antigravity CLI migration guide](https://antigravity.google/docs/cli/gcli-migration).
- **S5:** [Third-party endpoint notes](https://lzw.me/docs/opencodedocs/vbgate/opencode-mystatus/appendix/api-endpoints/) and [ai-limit-checker 0.8.0](https://pypi.org/project/ai-limit-checker/0.8.0/). Not confirmed; the PyPI page could not be loaded to confirm it.
- **S6:** [Cloud Quotas list reference](https://docs.cloud.google.com/docs/quotas/reference/rest/v1/projects.locations.services.quotaInfos/list) and [view and manage quotas](https://docs.cloud.google.com/docs/quotas/view-manage).
- **S7:** [Cloud Monitoring metrics, including Service Runtime](https://docs.cloud.google.com/monitoring/api/metrics_gcp_p_z).
- **S8:** [packages/core/src/code_assist/server.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/code_assist/server.ts) and [packages/core/src/code_assist/types.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/code_assist/types.ts).
- **S9:** [packages/core/src/code_assist/types.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/code_assist/types.ts) and [packages/core/src/config/config.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/config/config.ts).
- **S10:** [packages/core/src/config/storage.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/config/storage.ts), [packages/core/src/code_assist/oauth2.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/code_assist/oauth2.ts), and [packages/core/src/mcp/token-storage/index.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/mcp/token-storage/index.ts).
- **S11:** [packages/core/src/code_assist/oauth2.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/core/src/code_assist/oauth2.ts). Defines the CLI's OAuth client.
- **S12:** [packages/cli/src/ui/commands/statsCommand.ts](https://github.com/google-gemini/gemini-cli/blob/89e9d25d67e7dc7cb0deb84ad2c3474b7a392d31/packages/cli/src/ui/commands/statsCommand.ts) and [Gemini CLI quota and pricing](https://geminicli.com/docs/resources/quota-and-pricing/).
- **S13:** [Gemini API rate limits](https://aistudio.google.com/docs/rate-limits) and [API errors](https://aistudio.google.com/docs/api-errors).
- **S14:** [Gemini Apps limits](https://support.google.com/gemini/answer/16275805).
