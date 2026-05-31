# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0]

### Fixed

- **The base install no longer crashes on `import reqlient` when `redis` is not
  installed.** The `redis` package is an optional extra (`reqlient[redis]`) but
  was imported unconditionally at module load in both the sync and async circuit
  breaker modules, so `import reqlient` raised `ModuleNotFoundError: redis` on a
  plain install. `redis` is now imported lazily and only when a `redis_url` is
  actually configured; if it is missing, a clear "install reqlient[redis]"
  message is surfaced and the registry falls back to in-memory state.

- **The sync `RestClient` now releases its connection pools.** It gained a
  `close()` method and context-manager support (`with RestClient(...) as client:`),
  closing every per-thread `httpx.Client` it created. Previously the thread-local
  sessions were never closed, leaking sockets. (The async client already
  supported `aclose()` / `async with`.)

### Added

- **Retries now honor the server's `Retry-After` header** on `429`/`503`
  responses (both delta-seconds and HTTP-date forms) before falling back to
  computed backoff.
- **Backoff now uses equal jitter** (`base/2 + rand(0, base/2)`) instead of a
  fixed exponential delay, spreading retries across clients to avoid a
  synchronized thundering herd against a recovering service.

## [0.4.0]

### Changed (breaking)

- **Reworked the client configuration interface into grouped config objects.**
  `RestClient` and `AsyncRestClient` no longer take a long list of flat keyword
  arguments; resilience and transport settings are passed as small frozen
  dataclasses, one per concern: `TransportConfig`, `RetryConfig`,
  `CircuitBreakerConfig`, and `BulkheadConfig`. Each policy is enabled by passing
  its config object and disabled by passing `None`. Config-group arguments are
  keyword-only.

  ```python
  RestClient(
      base_url, service_name,
      transport=TransportConfig(timeout=30, verify_ssl=True),
      retry=RetryConfig(max_retries=3),
      circuit_breaker=CircuitBreakerConfig(fail_max=5),  # None to disable
      bulkhead=BulkheadConfig(max_concurrent=10),        # None/omit to disable
  )
  ```

  Removed constructor arguments (no backward compatibility): `timeout`,
  `verify_ssl`, `default_headers` → `transport`; `max_retries`,
  `retry_backoff_factor`, `retry_status_codes` → `retry`; `use_circuit_breaker`,
  `breaker` → `circuit_breaker`; `use_bulkhead`, `max_concurrent_requests`,
  `bulkhead_max_wait`, `bulkhead` (instance) → `bulkhead` (config). The async
  client keeps its `client=` (httpx.AsyncClient) injection point. Per-request
  `max_retries` / `retry_backoff_factor` overrides on the HTTP methods are
  unchanged.

  Retry and circuit breaker are enabled by default; the bulkhead is off by
  default. Passing `retry=None` now fully disables the retry behavior.

### Removed

- Dead `default_retry_config` attribute and the unused `verify_ssl` parameter
  threaded through the pipeline builders / `AsyncHttpBehavior`.

## [0.3.1]

### Fixed

- **Redis-backed synchronous circuit breaker now actually uses Redis.** The
  `CircuitRedisStorage` was constructed with an invalid argument
  (`redis_client=` instead of `redis_object=`, and a missing required initial
  `state`), which raised `TypeError` and was silently swallowed — so every sync
  breaker fell back to in-memory storage even when Redis was configured. The
  constructor is now called correctly, with `fallback_circuit_state=STATE_CLOSED`
  so the breaker fails open (treats the circuit as closed) if Redis becomes
  unreachable at runtime.

## [0.3.0]

### Added

- **Bulkhead (concurrency isolation) pattern.** Both `RestClient` and
  `AsyncRestClient` can now cap the number of concurrent in-flight requests to a
  service, so a slow or failing dependency cannot exhaust local resources and
  starve calls to other services. It is **opt-in** (`use_bulkhead=False` by
  default), so existing behavior is unchanged.
  - New config on both clients: `use_bulkhead`, `max_concurrent_requests`,
    `bulkhead_max_wait` (seconds to wait for a slot before rejecting; `0` =
    reject immediately), and an explicit `bulkhead=` injection point.
  - New `Bulkhead`/`BulkheadRegistry` and `AsyncBulkhead`/`AsyncBulkheadRegistry`
    (in-memory, per-service semaphores; bulkheads protect *local* resources, so
    state is intentionally per-process — not Redis-backed). Configure registry
    defaults via `BulkheadRegistry.configure(...)`.
  - New `BulkheadFullError` (a `RestClientError`, intentionally **not**
    retryable) raised when the bulkhead is full.
  - **Pipeline placement:** the bulkhead sits *outside* the circuit breaker, so a
    full bulkhead (local overload) is never counted as a downstream failure that
    would trip the breaker, and requests that fail request-validation do not
    consume a concurrency slot.

## [0.2.0]

### Changed

- **Sync HTTP backend migrated from `requests` to `httpx`.** The synchronous
  `RestClient` now uses `httpx.Client` instead of `requests.Session`. This
  unifies the sync and async clients on a single HTTP library (the async client
  already used `httpx`). The public API (`RestClient`, its `get`/`post`/`put`/
  `patch`/`delete` methods, constructor arguments, interceptors, and the
  reqlient error hierarchy) is unchanged.
- TLS verification (`verify_ssl`) and redirect-following are now configured on
  the underlying client at construction time. `follow_redirects=True` is set so
  that redirect-following matches the previous `requests` default behavior.

### Fixed

- Each `RestClient` instance now owns its own `httpx.Client` (kept thread-local
  for per-thread connection pools) instead of sharing a single process-global
  client. Previously, because `httpx` bakes `verify`/`timeout` into the client
  at construction, multiple clients created on the same thread with different
  `verify_ssl`/`timeout` settings would silently reuse the first client's
  settings — e.g. a client created with `verify_ssl=False` could still verify
  certificates. Per-instance clients now honor their own configuration.

### Removed

- `requests` is no longer a dependency of reqlient. `httpx` is now a base
  dependency (it was previously an optional `async` extra).

### Migration guide (0.1.x → 0.2.0)

For typical usage — construct a client, call `get`/`post`/etc., and catch
reqlient's own error types — **no code changes are required**. The behavioral
defaults (redirects, timeouts, retries, circuit breaker, validation, logging)
are preserved.

Review the following if they apply to your project:

1. **Transitive `requests` dependency.** If any of your code does
   `import requests` while relying on reqlient to install it, add `requests` to
   your own project's dependencies — reqlient no longer pulls it in.
2. **Direct access to `client.session`.** This property now returns an
   `httpx.Client` rather than a `requests.Session`. Any `requests`-specific
   usage of it must be updated to the `httpx` equivalent.
3. **Direct construction of internal behaviors.** `HttpBehavior` no longer takes
   a `verify_ssl` argument (TLS verification is configured on the client);
   its signature is now `HttpBehavior(session, timeout)`. Normal client usage is
   unaffected.
4. **Proxy / environment variables.** `httpx` reads `HTTP_PROXY` / `HTTPS_PROXY`
   / `NO_PROXY` with slightly different rules than `requests` (env proxies are
   gated behind `trust_env`). Verify proxy behavior if you depend on it.
5. **Connection pooling.** Connection pools are no longer shared across separate
   `RestClient` instances on a thread; each client owns its pool. This is
   normally transparent, but relevant if you create many clients and relied on a
   shared pool.

Recommended verification before upgrading: run your own test suite against
0.2.0, grep for `import requests` and any `.session` access on a reqlient
client, and smoke-test paths most likely to differ — anything behind a proxy,
anything using `verify_ssl=False`, and any redirecting endpoints.

### Notes

- This release only changes the synchronous client. The asynchronous
  `AsyncRestClient` already used `httpx` and is unchanged.

## [0.1.8]

- Baseline release prior to the `httpx` migration.
