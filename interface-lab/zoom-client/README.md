# Zoom Client View

The product uses React 19. The iframe is a separate, pinned build of Meeting SDK
6.10.0 and its React 18.3.1 peers. Do not add the SDK to the product's React tree.

`npm ci` in `interface-lab` installs this package from its own lockfile.
`npm run build` and `npm run storybook` build it first. Standalone build:

```sh
npm run zoom:build
```

Storybook copies `dist/call/zoom` into the public release. This directory contains
hashed JS chunks, the complete npm SDK `dist/lib` and `dist/ui`, local helper pages,
licenses, and a generated manifest with SHA-256 digests. Never edit `dist`.

## Connection sequence

1. Mount one hidden same-origin iframe on the prejoin screen. Its `ready` message
   means the message transport is ready, not that the SDK has loaded.
2. Send `prepare`, without credentials. Fetch the pinned local chunks, locale,
   styles, media scripts and browser-appropriate audio/video WASM. HTTP errors,
   stylesheet failures and invalid WASM prevent `prepared`. SDK preload callback
   completion alone is not a resource-readiness signal.
3. Only after explicit entry and consent, request fresh access from the backend.
   Send it in memory to this exact iframe using the checked origin and source.
   Reuse preparation, then call `init` followed by a single `join`.
4. Accept `joined` only from the SDK connection result, never from preload/init.
   Device permission prompts and the in-call UI belong to Zoom.
5. A loading failure permits an explicit retry in a new iframe. Ordinary parent
   re-renders cannot retry, duplicate a join, or deliver new credentials to an
   already joined frame. Leave/unload cancels preparation and ignores late results.

The 15-second transport timeout and 90-second preparation deadline are separate.
They do not limit time in the waiting room or the SDK's own reconnection flow.

## Verification

From `interface-lab`:

```sh
npm run zoom:build
node --experimental-vm-modules --test scripts/zoom-client.test.mjs
node --test scripts/zoom-build.test.mjs
npx playwright install chromium
node --test scripts/zoom-frame.component.test.mjs scripts/zoom-participant.component.test.mjs scripts/zoom-recruiter.component.test.mjs scripts/zoom-sdk.browser.test.mjs
```

The last test uses the real local SDK, blocks all external requests, and injects
CSS/WASM outages and invalid WASM. It never joins a real meeting. Set
`PLAYWRIGHT_CHANNEL=chrome` to use installed Chrome, or
`PLAYWRIGHT_BROWSER=webkit` for the SDK resource tests after installing WebKit.

Browser tests and a successful build are not acceptance of two-way media. Verify
candidate and host on separate devices/networks, waiting room, device permissions,
network recovery, local leave, recording and material delivery on the exact
published release before declaring end-to-end acceptance.

## Deployment

Install the updated reviewed receiver before shipping: it allows the SDK's WASM,
locale and media file paths and accepts up to 96 MiB compressed / 256 MiB expanded.
Its 25-hour retention keeps previous hashed Zoom chunks and versioned media
available to already open tabs, without replacing current files or renewing TTL.

The Caddy configuration compresses resources and caches successful responses for
hashed chunks and the pinned SDK version. HTML/bootstrap remain revalidated;
missing files are not given immutable caching. Validate the configuration with the
server's installed Caddy before applying it. An SDK upgrade requires reviewing
peer versions, asset layout, browser feature variants, cache paths, package
allowlists and these tests together.
