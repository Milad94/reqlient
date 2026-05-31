# Plan: De-duplicating the sync and async implementations

## Problem

The sync and async code paths are ~80% parallel copies of each other:

| File pair | Lines (sync / async) | Notes |
|---|---|---|
| `sync/behaviors.py` / `async_/behaviors.py` | 482 / 474 | every behavior duplicated |
| `sync/rest_client.py` / `async_/rest_client.py` | 501 / 541 | pipeline build, request prep, methods |
| `sync/bulkhead.py` / `async_/bulkhead.py` | 138 / 123 | registry + semaphore wrapper |
| `sync/circuit_breakers.py` / `async_/circuit_breakers.py` | 181 / 506 | **different implementations** (see Phase 4) |

Cost: bugs and changes must be applied twice and silently drift. Real examples
from history: the `verify_ssl` dead-param, the `request_schema` vs
`request_data_schema` typo, and exception-mapping differences all had to be
fixed in two places (or were fixed in only one).

## Key insight

Almost all of the duplicated logic is **pure / non-I/O** and therefore
transport-agnostic. The only thing that genuinely differs between sync and async
is the single line that calls the next stage / makes the HTTP call (`x` vs
`await x`). Everything else — URL joining, request serialization, schema
validation, status-code → error mapping, error-context creation, idempotency-key
generation, backoff math, and turning an `httpx.Response` into a
`ResponseContext` — has no `await` in it and can be shared verbatim.

## Recommended strategy

**Phase 1–3 (incremental, low risk): extract the pure logic into `core/` and
make the sync/async behaviors thin wrappers around it.** This removes the bulk of
the duplication with no change in behavior and the existing 235 tests as a
safety net.

**Phase 5 (optional, larger): adopt `unasync`** (the codegen approach used by
httpx/urllib3/httpcore) to generate the sync variant from the async source for
whatever thin wrappers remain. Only worth it if duplication is still annoying
after Phases 1–3.

Do **not** try to merge the actual I/O boundary or the httpx sync/async clients —
that is the irreducible difference and should stay explicit.

## Phased steps

### Phase 1 — Extract pure helpers into `core/` (no behavior change)
Create `core/pipeline_logic.py` (or similar) with transport-agnostic functions,
each already present (twice) in the behaviors today:

- `build_url(base_url, endpoint) -> str`
- `build_request_context(method, endpoint, ...) -> RequestContext`
- `validate_request_schema(request)` / `validate_response_schema(request, response)`
- `status_code_to_error(response) -> None | raises` (the 401/403/404/429/5xx/4xx ladder)
- `response_from_httpx(httpx_response, request) -> ResponseContext` (incl. the JSON/`raw_content` fallback and exception mapping helpers)
- `add_idempotency_key(request)`
- `backoff_seconds(retry_count, backoff_factor) -> float`
- `is_retryable_status(status, retry_status_codes) -> bool`

These are copy-paste-identical between the two `behaviors.py` files today, so this
is mechanical. **Risk: low.** Add focused unit tests for each helper.

### Phase 2 — Rewire `sync/behaviors.py` onto the helpers
Each sync behavior keeps its class + `handle()` but delegates the logic to the
Phase-1 helpers. The class shrinks to "call helper, call `_handle_next`, call
helper". Run the full suite. **Risk: low** (sync tests cover it).

### Phase 3 — Rewire `async_/behaviors.py` onto the same helpers
Identical to Phase 2, but `handle()` is `async` and awaits `_handle_next`. The
helpers are reused unchanged. After this, the only difference between the two
behavior files is `async`/`await` and the http call. **Risk: low.**

### Phase 4 — `rest_client.py` shared request prep
Extract the duplicated `__request` preamble (URL build, header merge, request-data
serialization, `RequestContext` construction) and the response-model construction
(`TypeAdapter` / `model_validate`) into shared helpers. The pipeline-builder
methods are structurally identical too and can share a small builder that takes
the behavior classes as parameters. **Risk: medium** (touches the hot path; the
integration tests cover it).

### Phase 5 — Unify the two circuit breakers (separate track)
This is the one pair that is *not* a copy: sync wraps `pybreaker` (~180 lines),
async is a hand-rolled 500-line state machine that already behaves differently
(it only trips on `RetryableError`; pybreaker trips on any non-excluded
exception). Pick one model and share the state machine + storage interface, with
sync/async adapters over Redis. **Risk: medium-high**; do it on its own, with new
tests pinning identical open/half-open/close semantics across sync and async.
This also fixes the flaky timing tests (inject a clock instead of `asyncio.sleep`
on wall time).

### Phase 6 (optional) — `unasync`
If the remaining `async`/`await`-only divergence is still worth removing, write
the async source and generate sync via `unasync` at build time. One source of
truth; the sync file becomes generated. Adds a build step + CI check that the
generated file is up to date.

## Sequencing & effort

| Phase | Effort | Risk | Payoff |
|---|---|---|---|
| 1. Extract helpers | M | Low | Foundation |
| 2. Sync behaviors → helpers | S | Low | ~40% of behaviors dup gone |
| 3. Async behaviors → helpers | S | Low | ~80% of behaviors dup gone |
| 4. rest_client shared prep | M | Med | Removes hot-path dup |
| 5. Unify circuit breakers | L | Med-High | Consistent semantics + fixes flakiness |
| 6. unasync (optional) | L | Med | Single source of truth |

Recommended order: **1 → 2 → 3** first (biggest win for least risk), ship it,
then schedule **5** (breaker unification) on its own, and treat **4** and **6** as
follow-ups. The existing test suite is the safety net — keep it green after every
phase and add helper-level unit tests in Phase 1.
