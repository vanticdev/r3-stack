# Onshape integration

Reserved for importing immutable CAD revision references, geometry, assembly
identities, joint relationships, placement, and available mass/inertia data into
the shared robot model. Keep provider-specific API details within this adapter.

Documentation only. API capabilities and export formats remain to be verified.
A visualization mesh alone is insufficient for engineering or kinematic data.

See [repository layout](../../docs/architecture/repository-layout.md).

## Headless use

On servers and in CI, use the official API keys: `ONSHAPE_ACCESS_KEY` /
`ONSHAPE_SECRET_KEY`, optionally `ONSHAPE_BASE_URL` and
`R3_ONSHAPE_TRANSPORT=api-keys`. Requests are HMAC-SHA256 signed
(`r3_onshape.client.ApiKeyTransport`); basic auth is available with
`ONSHAPE_API_AUTH=basic`. CAD images come from the REST shaded-view endpoints
(`r3_onshape.views`) as `build/cad/views/{iso,front,top,right}.png`, never from
a browser. The `onshape-native` bridge is a local-development convenience only.
See [headless operation](../../docs/architecture/headless.md).
