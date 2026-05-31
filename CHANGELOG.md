# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
