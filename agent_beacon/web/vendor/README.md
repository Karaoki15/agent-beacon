# Vendored QR encoder

- Package: `qrcode-generator` 2.0.4, MIT, Kazuhiko Arase.
- Registry source: `https://registry.npmjs.org/qrcode-generator/-/qrcode-generator-2.0.4.tgz`.
- Registry integrity (verified before extraction): `sha512-mZSiP6RnbHl4xL2Ap5HfkjLnmxfKcPWpWe/c+5XxCuetEenqmNFf1FH/ftXPCtFG5/TDobjsjz6sSNL0Sr8Z9g==`.
- Vendored file: `qr.js`, extracted from `package/dist/qrcode.js` only.
- `qr.js` SHA-256: `79ec86f82856005b1c887905cfccfcfbec3821ca61c7fd5a952faa5f778f791c`.
- License: `LICENSE.qrcode-generator.txt`, from the upstream repository's MIT license.

The browser loads this local file; QR generation has no network dependency. The UI paints the returned matrix onto a canvas and includes a four-module quiet zone. The QR encodes only the short-lived, single-use native pairing URI.
