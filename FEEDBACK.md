# Feedback on Nebius Token Factory and NVIDIA Nemotron

Written while building Patchwright. Items marked (observed) happened to us; items marked (todo) are filled in after the Nebius benchmark runs.

## What worked well

- (observed) Token Factory is OpenAI-compatible. The OpenAI Python SDK worked unchanged with only a base URL and key change.
- (observed) The model list endpoint returned the Nemotron 3 Nano, Super and Ultra models for our key, so model selection could be automated (`python -m patchwright configure`).
- (observed) Calls to all three tiers returned in about one to two seconds for a trivial prompt.

## Friction and suggestions

- (observed) The `/models` response has no pricing field. We had to read prices back from the usage page (cost divided by usage). Per-million input and output prices in the model list would let any tool report accurate cost per run.
- (observed) The usage table lags the balance: after a few hours of benchmarking the balance had dropped by about $11.45 while the usage table still showed a $1.84 total. A clear "updated at" note or live totals would avoid surprises when someone is watching their credit.
- (observed) The Usage page itself is excellent for this kind of work: it separates input, output and cached tokens per model per region, which is how we verified our own cost accounting.
- (observed) Billing details, including a card, are required before an API key works. For a hackathon this blocked participants who had credits but no card; a card-free credit voucher path would help.
- (observed) The login page offers SSO, Google and GitHub only; users on other mail providers must first create a Google or GitHub identity.
- (observed) Concurrency: up to 22 sandboxes calling Token Factory in parallel completed with no crashed runs. Our client retries transient rate limits and does not count them, so we cannot say how often they occurred. Exposing rate-limit headers or a documented limit would let clients pace themselves.
- (observed) Nemotron 3 Super sometimes double-escaped quotes inside the JSON string of a "write file" action, which broke parsing and cost about 2,000 tokens per occurrence. We fixed it on our side by sending file content in a raw block instead of inside JSON. A structured-output or JSON-mode option for Nemotron would remove this class of failure.
- (observed) Nemotron 3 Ultra's strongest property for agents was how few turns it needed: it fixed bugs in 6 to 13 steps one action at a time, and in roughly 3 to 12 steps with several actions per reply. Because the conversation is re-sent every call, that made it cheaper per fixed bug than expected.

## NVIDIA

- (observed) The free hosted endpoint at build.nvidia.com returned 404 for some listed models (for example Nemotron 3 Super) on a developer key, and was rate limited (HTTP 429) under parallel load, which made it unsuitable for benchmarking and a reason to move to Token Factory.
- (observed) Nano's difficulty judgments did not predict which bugs a larger model would fail: asked to rate bugs easy/medium/hard it called every one easy or medium, and the larger model failed on half of each group. It was, however, reliable at refusing non-bug requests (10 of 10 in a small hand-written check).

## Tavily

- (observed) `include_domains` works well for bug research: restricting searches to developer sources (GitHub, Stack Overflow, Python docs, PyPI) replaced a pest-control page with the project's own GitHub issues for the same query.
- (observed) Once or twice a search returned HTTP 200 with an empty result list and succeeded on retry. We now retry, and fall back to an unrestricted search when the restricted one is empty, but a documented `results: []` reason would help.
- (observed) The usage endpoint (`/usage`) made it easy to confirm how many credits our runs used.
