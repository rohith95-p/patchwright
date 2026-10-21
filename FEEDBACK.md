# Feedback on Nebius Token Factory and NVIDIA Nemotron

Written while building Patchwright. Items marked (observed) happened to us; items marked (todo) are filled in after the Nebius benchmark runs.

## What worked well

- (observed) Token Factory is OpenAI-compatible. The OpenAI Python SDK worked unchanged with only a base URL and key change.
- (observed) The model list endpoint returned the Nemotron 3 Nano, Super and Ultra models for our key, so model selection could be automated (`python -m patchwright configure`).
- (observed) Calls to all three tiers returned in about one to two seconds for a trivial prompt.

## Friction and suggestions

- (observed) The `/models` response has no pricing field, so a cost-tracking client cannot read per-token prices from the API. Adding input and output price per million tokens to the model list would let tools report accurate cost per run.
- (observed) Billing details (including a card) are required before an API key can be used. For a hackathon this blocked some participants who had credits but no card; a clear note on the hackathon page, or a card-free credit voucher path, would help.
- (observed) The login page offers SSO, Google and GitHub only; users with Outlook or other mail providers need to create a Google or GitHub identity first.
- (todo) Rate limits and concurrency behaviour under parallel sandboxes.
- (todo) Differences in how Nemotron Nano, Super and Ultra follow a strict "one JSON action per reply" instruction.
- (todo) Where Token Factory accelerated the workflow (latency, throughput, cost) with measured numbers.

## NVIDIA

- (observed) The free hosted endpoint at build.nvidia.com returned 404 for some listed models (for example Nemotron 3 Super) on a developer key, and was rate limited (HTTP 429) under parallel load, which made it unsuitable for benchmarking and a reason to move to Token Factory.
- (todo) Quality notes on Nemotron 3 tiers for agentic coding.
