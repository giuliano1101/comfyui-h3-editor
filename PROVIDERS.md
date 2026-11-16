# Provider and agent extension

Windows fork, modified 2026-11-17. Upstream baseline: `0d2e0d1fad9218cafdb599902be252f3254b2fa1`, release 0.9.1. This extends the upstream editor; it does not embed a second editor. The delivered ZIP was inspected as a three-file, process-local prototype. Its capability/state/confirmation ideas were reused selectively; its global-lock submission implementation was replaced.

## What is available

| Capability | Status |
|---|---|
| Existing local H3 requests, queue, clips, timeline and history | Preserved; local GPU generation not verified in this fork |
| Simulation | Integrated, offline, no video and no billing |
| Durable jobs, input limits, idempotency, quota, reserved budget, cancellation races | Implemented and tested |
| MiniMax **v1 Hailuo** text-to-video | Implemented against official docs with mocked transport and loopback HTTP; live account/API unverified; disabled by default |
| New MiniMax H3 v2, other vendors, image/reference/audio generation | Not implemented; never advertised as supported |
| Custom relay | Only explicitly selected `minimax-video-v1`, operator-approved exact HTTPS hosts; compatibility and billing unverified |
| Output staging and new-take import | Tested with synthetic MP4, existing project locks, revision conflict, previous takes and history |
| MCP stdio | Read-only by default; project-scoped, browser-approved mutations, tested protocol and HTTP bridge |
| Voice | Local opt-in recording controls and typed/pasted transcript preview; hardware microphone unverified; automatic transcription and voice chat unavailable |

See [Windows verification](docs/WINDOWS.md), [security boundaries](docs/SECURITY-PROVIDERS.md) and [delivery record](docs/DELIVERY.md).

## Configuration and credentials

No setup, provider account, model download or paid request is performed automatically. `Providers & agent` leaves local H3's Render controls intact. A simulation completes without output, so it cannot be imported as a video.

Private state is in `<ComfyUI user>/h3-provider/`, outside output and the `/view` route:

- `providers.json`: operator-owned **non-secret** configuration, read once at startup. Alternatively use the `H3_PROVIDER_CONFIG_PATH` environment variable to choose this file.
- `jobs.sqlite3`: task IDs, state, model/parameters, project/clip IDs, timestamps, request fingerprint and budget reservations. No prompt text, keys, signed URLs or raw provider errors.
- `staging/`: bounded validated MP4 results, with incomplete files removed. Staging is not a static route. Files stay here for explicit import/retry; no automatic purge or retention service is installed.

Start with [providers.example.json](providers.example.json). It deliberately enables **no paid service** and contains **no sample price**. A user/operator must verify their account's tariff and supply an integer USD-cent quote per supported combination, keyed as `MODEL:RESOLUTION:SECONDS` (for example the *key* `MiniMax-Hailuo-2.3:768P:6`). An absent quote blocks that combination. Set a lifetime `budget_cents`, approve exact CDN hostnames in `download_hosts`, and only then opt in with `enabled: true`. The UI still requires review and cost confirmation for every submission. Server reservations are a conservative local limit, **not a guarantee or cap on the supplier's bill**. Reserve the maximum verified applicable tariff; taxes, account pricing and supplier changes remain external facts.

Set `MINIMAX_API_KEY` in the server process environment yourself. For a relay, choose a separate provider ID, its exact approved host and a dedicated environment variable such as `H3_RELAY_API_KEY`. Never put key values in this JSON, a project, browser fields, shell arguments, logs, Git or an agent conversation. Credential status exposes only configured/not configured. To revoke: disable the provider in configuration, remove its environment variable from the launcher/OS environment yourself, restart the server, and revoke the key in the provider console if needed. Stopping/removing a key does not cancel already accepted jobs. This fork does not implement an OS credential-vault integration.

Use a private per-user Windows directory/ACL for state and config. The code inherits the directory's Windows permissions; it does not claim Unix `chmod` enforces Windows access control. One local server process owns this database. Do not run two editor servers against it.

## Protocol record — reviewed 2026-11-17

Official sources: [create](https://platform.minimax.io/docs/api-reference/video-generation-t2v), [query](https://platform.minimax.io/docs/api-reference/video-generation-query), [download](https://platform.minimax.io/docs/api-reference/video-generation-download). These are the legacy **v1** Hailuo API, not the newer H3 v2 API.

Implemented models are `MiniMax-Hailuo-2.3` and `MiniMax-Hailuo-02`: text input up to 2,000 characters, 6 or 10 seconds at 768P, 6 seconds at 1080P. Image, references, callbacks, audio and aspect-ratio parameters are deliberately not accepted.

```http
POST https://api.minimax.io/v1/video_generation
Authorization: Bearer <server-only credential>
Content-Type: application/json

{"model":"MiniMax-Hailuo-2.3","prompt":"<reviewed text>","duration":6,"resolution":"768P","prompt_optimizer":false}
```

Successful submission returns `task_id` and `base_resp.status_code: 0`. Poll `GET /v1/query/video_generation?task_id=…`, no more often than once per task per 10 seconds. Map `Preparing`/`Queueing` to queued, `Processing` to running, `Success` to succeeded with `file_id`, and `Fail` to failed. Unknown values/IDs and nonzero/malformed `base_resp` are refused. Retrieve `GET /v1/files/retrieve?file_id=…`, validate the file ID and purpose, then download its `file.download_url` **without credentials**, through the separate approved-host policy. A refreshed URL is retrieved when needed; it is not persisted.

No documented cancellation or remote submission-idempotency endpoint was established for this v1 contract. Cancellation is unsupported for this adapter. An interrupted POST must be reconciled in the supplier account; never retry it automatically. Account-specific upstream rate limits were not verified: local defaults are two active tasks per connection, two globally, 20 daily submissions per connection, 10 submit requests/minute and 30 status requests/minute. 429/HTTP/provider errors are sanitized and no request is automatically retried.

## Task lifecycle and result import

`submitting → queued → running → succeeded/failed/cancelled`. A stale queue status cannot reverse running; terminal states are final. Submission failure becomes `unknown` and remains recorded, occupying its active slot/reservation. A server restart also turns interrupted `submitting` records into `unknown`; queued tasks retain their provider task IDs. Reconciliation attaches an account-verified existing task ID. It never sends another POST. There is no unsafe “assume not billed” or “clear unknown job” action.

Unconfirmed cancellation remains `cancel_requested`; timeout means `timed_out`, **not** externally cancelled or refunded. Both can still be polled to a real terminal outcome. Submission, polling and cancellation run outside the database lock; a per-job in-flight guard serializes poll/cancel races. The SQLite journal commits a submission reservation before network I/O. Idempotency keys are bound to connection identity and a one-way request fingerprint.

Download is explicit, limited to 256 MiB and MP4 MIME/signature, then probed/decoded in a 20-second subprocess with dimension/fps/duration bounds. Network operations have socket and wall-clock deadlines, reject redirects/proxies/non-public addresses, validate every resolved A/AAAA, pin the actual peer and preserve TLS hostname verification. Normalization is bounded to 120 seconds and uses the project's existing FFmpeg path. A separate user-approved import adds a **new take** through `accept_take` and `commit`, preserving old takes. It checks the revision before conversion and again at commit. Linked H3 clips are refused because remote text-to-video cannot fulfill their held-frame contracts. A failed/conflicting import retains the staged result and removes uncommitted derived files.

Jobs are outside project history. Undo/restore cannot cancel a provider task, return money or undo disclosed data. Review the provider account after an uncertain result. Neither unknown jobs nor spent reservations are silently erased on restart.

## MCP

`tools/h3_mcp.py` implements newline-delimited UTF-8 JSON-RPC stdio using MCP protocol `2025-06-18`; no network MCP listener or extra dependency. Sources: [stdio transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports), [tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

Configure `H3_MCP_TOKEN` (at least 32 random characters) in both server and MCP subprocess environments; keep it out of chat/command arguments/config committed to Git. `H3_MCP_PROJECTS` is an exact comma-separated project-folder allowlist. With no allowlist, no project is readable. `H3_MCP_ALLOW_EDITS` defaults to off. `H3_EDITOR_PORT` defaults to 8188 in the MCP subprocess. Set these yourself in private launcher configuration, not in model-visible tool arguments.

Client command: your Python executable, argument `tools/h3_mcp.py` as an absolute path. The bridge always connects to `127.0.0.1`, ignores proxies and refuses redirects.

Read tools: summary, history, local render status, export plan and provider job summaries. With operator editing enabled, `propose_*`/`execute_*` wrap batches (maximum 20 strictly schema-allowed operations), restore, staged take import, export and local render cancellation. A proposal binds the exact payload, connection and revision, expires after five minutes, and is usable once. Only the browser session can approve; the agent cannot approve itself. Concurrent user edits return 409. Exports use the approved revision snapshot. There is no paid-generation, arbitrary path, remote URL or executable VFX tool. Full natural-language planning belongs to the caller, not an implicit intent parser in the server.

Legacy human-operated `skills/h3-editor/scripts/h3.py` remains usable with a transient, in-memory local editor session. It has the local user's full editor authority and must not be substituted for the restricted MCP connection. Cookies never persist to disk. Remote URLs are refused by this fork.
