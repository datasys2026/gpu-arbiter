# Routing

The arbiter resolves requests in two ways:

1. Match the request body `model` field against a configured model id.
2. Match the request path against a configured `route`.

If both are present, explicit `model` id resolution wins.

## Request order

For a matched route, the arbiter:

1. Acquires the global GPU lock.
2. Runs the configured unload hook.
3. Waits for the configured health hook.
4. Checks available VRAM.
5. Proxies the request to the upstream service.

## Path handling

The arbiter forwards the original method, body, headers, and query string to the upstream
service, while stripping hop-by-hop headers such as `Host` and `Content-Length`.

