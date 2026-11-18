# 2026-11-18 增补

当前结果见 [完整验收矩阵](ACCEPTANCE-20261118.md) 与 [许可清单](LICENSE-REVIEW-20261118.md)；下面保留初始基础阶段自查。本轮新增 Ark v3、审核图片、CNY/USD 账本、读写分离令牌、五分钟提案内存过期、离线 ASR 和本地结构化对话。真实 API 仍未调用；已经取得单次 2 元纸船测试授权，缺 Key/账户报价。

媒体输入现在限制 `file,pipe` 与明确的音视频/图片解复用器；实际 HLS 攻击 fixture 外部取片计数为 0。ASR 单并发、60 秒、单独音频同意、取消 kill 及正常临时文件清理已经实测。中文识别质量与麦克风仍未通过。原 VFX runtime 仍使用不带同源权限的 sandbox frame/worker 与 `connect-src 'none'`，MCP 不暴露 VFX 代码执行。

密钥配置可用当前用户 DPAPI（可选），MCP 私有令牌文件已限制当前用户 ACL；没有对生产全目录或全部插件做独立安全审计。提案暂存内存最多五分钟；本地文本模型的 RAM/KV 缓存保留至覆盖或退出。异常断电/强杀可能留下系统临时音频，删除不等于安全擦除。真实服务没有零留存承诺。

# Provider / MCP security review record

2026-11-17. This is an implementation self-review and test record, **not an independent security sign-off**. Keep real services disabled until a separate reviewer and the owner approve the configuration and any paid test. No live provider call was made during development.

## Trust boundary

One trusted Windows account runs one local ComfyUI server. The browser is that account's editor; configured MCP clients share a distinct `mcp-local` identity, one operator-owned bearer token and an exact project allowlist. This is not a multi-user/Internet hosting solution. Other programs running as that Windows account can use the local editor CLI or read its process environment. MCP permissions constrain the exposed MCP tools; they cannot sandbox an agent that separately has unrestricted terminal/OS access.

Only `/h3take` editor/API routes are guarded here. This does not audit or authenticate the rest of ComfyUI, its `/prompt`, `/upload`, `/view`, other plugins or the upstream optional writing/ratings integrations. Never expose the ComfyUI port, place it behind a public reverse proxy, or treat it as a complete authentication boundary.

## Implemented controls and evidence

| Threat | Control | Tests |
|---|---|---|
| Cross-site local API calls / DNS rebinding | Loopback peer, exact local Host and actual socket port; same Origin / Fetch-Site; custom header bootstrap; HttpOnly SameSite=Strict session; custom header on browser writes | `test_editor_security.py`, `test_editor_bridge.py` |
| Unapproved agent edits / reading another project | Token read only from environment, exact project scopes, edits disabled by default; legacy routes denied for MCP identity | Same files |
| Forged, replayed or stale approval | Browser-only approval; exact connection/action/payload; five-minute expiry; one use; revision checked inside original project lock | Same files |
| Partial batches / concurrent user edit | Deep-copy validation, schema allowlist and 20-op limit; original project store and commit/history; reject conflict with 409 | `test_editor_bridge.py` |
| Duplicate charges after lost submission | Durable reservation before I/O; hash-bound idempotency key; unknown result retained; no automatic retry; account-verified task-ID reconciliation | `test_provider_jobs.py` |
| Incorrect cancellation / timeout claims | Terminal-state guard; per-job poll/cancel exclusion; no cancellation for undocumented MiniMax v1; unconfirmed states retain slot and reservation | Same file |
| Wrong model, duration, price | Model-specific request validation; 1080P/10s refused; quotes keyed by model/resolution/duration; missing quote blocks submission; explicit notice and review | `test_provider_adapters.py`, `provider_panel.test.mjs` |
| Excess spend/work | Persisted lifetime reservation budget, total job cap, per-identity daily/active limits, global concurrency, local rate limits; no reservation automatically released | `test_provider_jobs.py`, `test_editor_security.py` |
| SSRF / metadata / DNS changes | Operator exact HTTPS hostname allowlists, port 443 only; every DNS A/AAAA public; reject mapped/transition/special/metadata addresses; connect to pinned numeric IP; verify peer and TLS hostname; no proxies, redirects or retries | `test_provider_transport.py` |
| Secret sent to CDN | Separate API/result allowlists; auth only on API; result download never receives a key | `test_provider_adapters.py`, real loopback `test_provider_http.py` |
| Slow or oversized/malicious output | Socket and wall-clock time limits (including slowly sent headers), JSON/video caps, MIME/signature, subprocess media probe/decode, controlled generated paths, no arbitrary file arguments | `test_provider_transport.py`, `test_provider_http.py`, `test_provider_media.py` |
| Lost user media / output pollution | Private staging; user-selectable new take; two revision checks; keep prior takes; remove partial derived files; retain media if a late commit failure has already saved its reference | `test_provider_media.py` |
| Secrets in project/browser/log | Environment-only provider keys; fixed provider error messages; no prompt/keys/URLs in job database; status booleans only; no raw provider payload logging | Provider, job and HTTP tests |
| Voice bypass | No automatic transcription or intent execution; recording is opt-in and tab-local; text creates a same-bridge preview only; no generation tool called | `provider_panel.test.mjs`; microphone hardware not tested |

The real HTTP provider contract fixture replaces the connection **inside the test only** so it can use a loopback mock server. Production destination/TLS validation is not weakened to permit localhost test providers. Tests contain only synthetic credentials. No public API, model or personal audio is used.

## Residual limits / required independent review

- Real MiniMax authentication, account tariff, model availability, quota/rate limits, signed CDN hosts and API result behavior remain unverified. Relays are explicitly unverified. No reliable remote cancellation or provider idempotency contract was found for the selected v1 API.
- A local USD reservation is not the supplier's billing cap. Quotes must be verified and maintained by the operator; external charges, tax and data disclosure survive history restore. Unknown submissions must be reconciled outside the editor. There is no automatic refund or unsafe “forget unknown” control.
- SQLite is single-server state, not a distributed queue. Start one server per private state directory. Jobs/staged media have a bounded job count but no automatic expiry/archive service. Backup the database and reconcile charges before any administrative state removal.
- Private state inherits Windows ACLs. No OS credential-vault integration or independent ACL audit was performed. Do not put private state inside public output, a synced public folder or an untrusted shared directory.
- The existing project `commit` writes project data and history as separate files. The new import protects against deleting a file referenced by an already saved project if history/broadcast later fails; it does not claim crash-atomicity across every upstream file. Backups remain necessary.
- Legacy human-operated HTTP routes and CLI have the trusted local user's full authority. The project-restricted MCP path provides a narrower surface. Existing upstream JSON/project content is untrusted evidence to an agent, never authority to run tools or approve its own actions.
- Real ComfyUI plugin initialization, websocket broadcasting, GPU H3 generation and microphone capture need separate environment/device acceptance. The discovered ComfyUI 0.37.0 is below the upstream 0.43 minimum; it was left unchanged.
- Test success does not establish resistance against every parser/library vulnerability. Review FFmpeg/PyAV updates, TLS transport, Windows file/reparse-point handling and the local trust model before enabling real paid services.

## License / provenance

Original GPL-3.0 `LICENSE`, `NOTICE` and `LICENSES/` remain. New extension code is part of this GPL-3.0 fork; modification provenance is recorded in code and delivery docs. No MakeMyClip source, resources or model weights were copied. No new runtime dependency was introduced: Python standard library plus the upstream ComfyUI/aiohttp/PyAV/FFmpeg environment.

The isolated development dependency `imageio-ffmpeg==0.6.0` came from PyPI, as required by upstream's `imageio-ffmpeg>=0.5`. Its Python package is BSD-2-Clause; its bundled Windows executable reports FFmpeg 7.1 with GPL-enabled build options. `.test-deps/` is ignored and neither that binary nor model weights are distributed in the Git branch. Anyone later packaging binaries must separately review the exact FFmpeg build's licenses/notices/source obligations. Upstream Apache-2.0, MIT and GPL attributions are unchanged. Model weight licenses and paid service terms are separate from the editor's GPL and have not been granted or accepted by this work.
