/**
 * Deterministic regression guard for the CLS class of defect the 2026-07-25 launch audit flagged
 * against the LIVE demo.aetherlogik.com URL: "Lighthouse mobile reports CLS 0.154, attributed to
 * hero .webp images without width/height."
 *
 * That finding did NOT reproduce: `index.html`'s two hero `<img>` tags already carry `width`/
 * `height` (760x674 and 900x557 — see PR #27), and a Performance-trace measurement of the built
 * `dist/` AND of the live URL itself (mobile 375px, two throttle profiles) recorded ZERO
 * `LayoutShift` events in either case — CLS 0.000, not a rounded-down small number. So there is
 * nothing to "fix" in the images today.
 *
 * What WAS missing: nothing prevented a future edit from silently reintroducing exactly that
 * mismatch — swap the screenshot without updating the attributes, or vice versa — and the only way
 * anyone would notice is the next manual Lighthouse audit. This test is the guard: it decodes the
 * REAL pixel dimensions from the committed `assets-src/*.b64` sidecars (the source of truth —
 * `public/*.webp` is a gitignored build artifact `decode-assets.mjs` regenerates, see that
 * script's own header comment) and asserts the HTML's declared `width`/`height` are the exact same
 * numbers. A mismatch fails this suite instead of waiting for the next audit to catch it.
 *
 * jsdom performs no layout (see `styles.overflow.test.ts`'s own note on that same limitation), so
 * this cannot reproduce the browser's aspect-ratio-from-attributes behavior directly — it locks
 * the underlying invariant that behavior depends on: declared attributes == real pixels.
 */
import { describe, expect, it } from "vitest";
// Vite/Vitest `?raw` raw-source imports (same convention `styles.overflow.test.ts` already uses
// for `styles.css?raw`) — these resolve fine at build/test time; there is no `import/no-unresolved`
// rule registered in this workspace's eslint config to satisfy.
import html from "../index.html?raw";
import calendarB64 from "../assets-src/screenshot-calendar.webp.b64?raw";
import liveBookingB64 from "../assets-src/screenshot-live-booking.webp.b64?raw";

interface PixelSize {
  width: number;
  height: number;
}

/** Read one byte, failing loudly (not `undefined`-propagating) past the buffer's end. */
function byteAt(bytes: Uint8Array, index: number): number {
  const value = bytes[index];
  if (value === undefined) {
    throw new Error(
      `WEBP decode: expected a byte at offset ${index}, but the buffer is only ${bytes.length} bytes long.`,
    );
  }
  return value;
}

function ascii(bytes: Uint8Array, start: number, length: number): string {
  return String.fromCharCode(...bytes.subarray(start, start + length));
}

/**
 * Decode a WEBP's real pixel dimensions from its RIFF container (VP8 lossy / VP8L lossless / VP8X
 * extended — https://developers.google.com/speed/webp/docs/riff_container). Mirrors the header
 * check `decode-assets.mjs` already does for validity, extended to also read width/height.
 */
function webpDimensions(bytes: Uint8Array): PixelSize {
  if (bytes.length < 30 || ascii(bytes, 0, 4) !== "RIFF" || ascii(bytes, 8, 4) !== "WEBP") {
    throw new Error("WEBP decode: not a valid RIFF/WEBP container.");
  }
  const fourcc = ascii(bytes, 12, 4);
  if (fourcc === "VP8X") {
    const width = 1 + (byteAt(bytes, 24) | (byteAt(bytes, 25) << 8) | (byteAt(bytes, 26) << 16));
    const height = 1 + (byteAt(bytes, 27) | (byteAt(bytes, 28) << 8) | (byteAt(bytes, 29) << 16));
    return { width, height };
  }
  if (fourcc === "VP8L") {
    const b0 = byteAt(bytes, 21);
    const b1 = byteAt(bytes, 22);
    const b2 = byteAt(bytes, 23);
    const b3 = byteAt(bytes, 24);
    const width = 1 + (((b1 & 0x3f) << 8) | b0);
    const height = 1 + (((b3 & 0xf) << 10) | (b2 << 2) | ((b1 & 0xc0) >> 6));
    return { width, height };
  }
  if (fourcc === "VP8 ") {
    // Little-endian 16-bit width/height, each carrying a 2-bit upscale flag in its top bits.
    const rawWidth = byteAt(bytes, 26) | (byteAt(bytes, 27) << 8);
    const rawHeight = byteAt(bytes, 28) | (byteAt(bytes, 29) << 8);
    return { width: rawWidth & 0x3fff, height: rawHeight & 0x3fff };
  }
  throw new Error(`WEBP decode: unrecognized chunk fourcc "${fourcc}".`);
}

/** `atob` (global in both browsers and jsdom) decodes base64 to a JS "binary string". */
function decodeB64(b64: string): Uint8Array {
  const binary = atob(b64.replace(/\s+/g, ""));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

/** Pull the `width`/`height` attributes off the `<img src="{src}" ...>` tag in raw HTML. */
function declaredDimensions(sourceHtml: string, imgSrc: string): PixelSize {
  const tagMatch = new RegExp(`<img[^>]*src="${imgSrc}"[^>]*>`).exec(sourceHtml);
  expect(tagMatch, `no <img src="${imgSrc}"> tag found in index.html`).not.toBeNull();
  const tag = tagMatch![0];
  const width = /width="(\d+)"/.exec(tag)?.[1];
  const height = /height="(\d+)"/.exec(tag)?.[1];
  expect(width, `<img src="${imgSrc}"> has no width="..." attribute`).toBeDefined();
  expect(height, `<img src="${imgSrc}"> has no height="..." attribute`).toBeDefined();
  return { width: Number(width), height: Number(height) };
}

function assertDeclaredMatchesReal(imgSrc: string, b64Sidecar: string): void {
  const real = webpDimensions(decodeB64(b64Sidecar));
  const declared = declaredDimensions(html, imgSrc);
  expect(
    declared,
    `<img src="${imgSrc}"> declares ${declared.width}x${declared.height}, but the committed WebP ` +
      `actually decodes to ${real.width}x${real.height}. A mismatch here is exactly the class of ` +
      "bug that shifts layout once the real image loads (CLS) — fix the HTML attributes (or " +
      "re-derive them from the new screenshot) to match the real pixels.",
  ).toEqual(real);
}

describe("hero screenshots reserve their real dimensions (CLS guard — launch-audit finding 2026-07-25)", () => {
  it("screenshot-calendar.webp: <img> width/height match the decoded WebP", () => {
    assertDeclaredMatchesReal("/screenshot-calendar.webp", calendarB64);
  });

  it("screenshot-live-booking.webp: <img> width/height match the decoded WebP", () => {
    assertDeclaredMatchesReal("/screenshot-live-booking.webp", liveBookingB64);
  });
});
