# Vendored noVNC 1.4.0

This directory contains unmodified files from the npm package
`@novnc/novnc@1.4.0`:

- Tarball: `https://registry.npmjs.org/@novnc/novnc/-/novnc-1.4.0.tgz`
- SHA-256: `005d4676126a252fcdf6e4851433dc7913947542b7f8f8b941af594c9dcd94cc`
- License: MPL-2.0; the bundled `vendor/pako` tree is MIT.

The `lib/`, `core/`, `vendor/pako/`, `AUTHORS`, and license files are copied
from that package. `lib/rfb.js` is the CommonJS-transpiled npm entrypoint;
`core/rfb.js` is the browser-native ES module and is the runtime entrypoint
used by the AI Studio preload adapter.

The AI Studio v1.4.1 dashboard bundle originally imports
`https://esm.sh/@novnc/novnc@1.4.0/lib/rfb.js`. At container startup the
adapter copies this tree to `/app/ui/dist/vendor/novnc` and rewrites that
import to `/vendor/novnc/core/rfb.js`, keeping the VNC login screen
self-hosted.
