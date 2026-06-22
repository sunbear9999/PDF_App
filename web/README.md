# Papyrus Local Web Companion

The companion is an opt-in, same-process LAN interface for the project that is
currently open in desktop Papyrus. It never starts automatically.

## Runtime

1. Install `requirements-web.txt` into the Papyrus environment.
2. Build the client with `cd web/frontend && npm install && npm run build`.
3. Open **Settings → Web App**, choose a concrete private interface, and press
   **Start Web App**.
4. Scan the rotating QR code. The first paired browser to request editing owns
   the exclusive edit lease and places a reversible guard over the desktop UI.

The QR secret is carried in the URL fragment, exchanged once for an HttpOnly
session cookie, and never appears in server access logs. LAN HTTP is intended
only for trusted private networks; stop the server when it is not in use.

## Boundaries

- ASGI handlers communicate with live Papyrus state only through
  `QtCommandDispatcher`; SQLite and Qt services remain on the Qt main thread.
- Client-visible documents use opaque source IDs. Filesystem paths are resolved
  only inside the main-thread bridge.
- Uploaded PDF/video files are atomically stored in `<project>.pdfproj.assets/`.
- The REST API is versioned under `/api/v1`; live domain events use
  `/api/v1/events`.
- Web extensions are opt-in manifests. Qt plugin widgets are never executed in
  a browser.

No frontend asset is loaded from a CDN and the server sets a same-origin CSP,
Host/origin checks, CSRF protection, pairing rate limits, body limits, and
no-referrer policy.
