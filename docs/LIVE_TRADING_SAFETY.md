# Live Trading Safety

Live execution is not implemented. Phase 7 defines `live` only as a forbidden validation value so malformed paper configuration fails explicitly. There is no live broker, production order route, production WebSocket, external production FIX session, withdrawal, transfer, deposit, or automatic position-flattening path.

Paper mode cannot escalate to live through a base URL, hostname, environment flag, provider selection, or credential change. Trading origins and routes come only from an immutable provider/environment catalog. The OKX demo REST hostname is shared with production, so every allowed request must carry the official mandatory demo header; absence or mismatch is rejected as production access. Redirects, arbitrary URLs, userinfo, HTTP, nonstandard ports, environment-overriding query parameters, and unapproved routes fail.

Credentials remain local, are loaded only after all paper gates pass, and never enter source, manifests, hashes, logs, monitoring, exceptions, PostgreSQL, fixtures, or CI. The Phase 7 kill switch stops new paper submissions, records cancellation intent, reconciles late fills, preserves positions, and never assumes cancellation succeeded.

A future Phase 8 must be an independent milestone with a separate broker class and explicit design approval. It must not reuse paper enablement as live authorization and must add guarded production endpoint identity, separate credentials, notional/exposure controls, dry run, kill and recovery controls, pre/post risk summaries, and complete audit evidence. Until that work is separately approved and implemented, live execution remains unreachable.
