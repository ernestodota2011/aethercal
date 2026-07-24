#!/usr/bin/env node
/**
 * Decodes the base64 sidecars under assets-src/ into real binary files under public/ (E-01,
 * AetherCal-13 Ola 1) — a build/dev prerequisite, not a Vite plugin, so it stays trivial.
 *
 * Why this exists: the source of truth for these assets has to be something every publishing
 * path can carry byte-for-byte. Real binary files are ideal for a browser (cacheable,
 * lazy-loadable, no HTML bloat) but this branch is published through the GitHub Contents API via
 * MCP tools, which take file `content` as literal text — verified empirically (a probe file's
 * base64 string came back stored verbatim, not decoded) to NOT carry raw binary without
 * corruption. Base64 text sidecars are the one representation that survives every path: they are
 * plain text (safe for MCP, `git diff`, and PR review) AND they decode back to the exact original
 * bytes. This script is the one place that conversion happens, so `public/*.webp`/`public/og.png`
 * are always reproducible BUILD ARTIFACTS (gitignored — see .gitignore) rather than something
 * committed twice in two different shapes that could drift apart.
 *
 * `og.png` joined this pipeline (I-1 fix, Ola 1 landing polish): it used to be the one hand-
 * committed binary exception (see the .gitignore history) — fine while nobody had to *update* it,
 * but the moment its "FOUR VIEWS" copy went stale, re-publishing a raw PNG through the same MCP
 * path that corrupts binary content became the actual blocker. Converging it onto the same
 * base64-sidecar pattern as the screenshots is the root fix, not a one-off workaround.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Sidecars are wrapped at 76 chars/line (standard base64 formatting, RFC 2045 §6.8) rather than
// kept as one giant line: Buffer.from(..., "base64") ignores embedded whitespace/newlines when
// decoding (verified), so this is free — and a many-short-lines file is what makes `git diff` and
// PR review usable, and lets line-oriented tooling page through the sidecar instead of choking on
// a single tens-of-KB line.
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const srcDir = join(root, "assets-src");
const outDir = join(root, "public");

/** Every asset this demo needs decoded. Add a line here, not a bespoke script, for a new one. */
const ASSETS = ["screenshot-calendar.webp", "screenshot-live-booking.webp", "og.png"];

/**
 * Format-aware magic-byte validators, keyed by file extension. Fail loudly rather than silently
 * ship a corrupt/truncated image — checking only the leading magic bytes (Crisol gate round 5,
 * medium confidence) would still pass a TRUNCATED file, since those are the first thing written,
 * so each validator also checks a length/size field against what actually decoded.
 */
const VALIDATORS = {
  ".webp": (bytes, b64Path) => {
    // RIFF container header with the four-byte "WEBP" fourcc at offset 8
    // (https://developers.google.com/speed/webp/docs/riff_container). The RIFF chunk size at
    // bytes[4..8) (little-endian, the byte count of everything AFTER those 8 header bytes) has to
    // match what actually decoded — a sidecar cut off mid-base64 fails this even though its
    // header is intact.
    if (bytes.length < 12) {
      throw new Error(`decode-assets: ${b64Path} decoded to only ${bytes.length} bytes (too short to be a WebP).`);
    }
    const riff = bytes.subarray(0, 4).toString("ascii");
    const fourcc = bytes.subarray(8, 12).toString("ascii");
    const declaredSize = bytes.readUInt32LE(4);
    const actualSize = bytes.length - 8;
    if (riff !== "RIFF" || fourcc !== "WEBP" || declaredSize !== actualSize) {
      throw new Error(
        `decode-assets: ${b64Path} did not decode to a valid, complete WebP ` +
          `(RIFF="${riff}" fourcc="${fourcc}" declaredSize=${declaredSize} actualSize=${actualSize}). ` +
          "Re-generate the .b64 sidecar from a real screenshot before building.",
      );
    }
  },
  ".png": (bytes, b64Path) => {
    // Fixed 8-byte PNG signature (https://www.w3.org/TR/png/#5PNG-file-signature), followed by the
    // IHDR chunk, which MUST be first: bytes[8..12) is IHDR's 4-byte length (always 13, big-endian)
    // and bytes[12..16) is the ASCII tag "IHDR". A truncated sidecar loses the signature entirely
    // or decodes to too few bytes to hold even that first chunk header.
    const SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    if (bytes.length < 16) {
      throw new Error(`decode-assets: ${b64Path} decoded to only ${bytes.length} bytes (too short to be a PNG).`);
    }
    const signatureOk = bytes.subarray(0, 8).equals(SIGNATURE);
    const ihdrLength = bytes.readUInt32BE(8);
    const ihdrTag = bytes.subarray(12, 16).toString("ascii");
    if (!signatureOk || ihdrTag !== "IHDR" || ihdrLength !== 13) {
      throw new Error(
        `decode-assets: ${b64Path} did not decode to a valid PNG with a leading IHDR chunk ` +
          `(signatureOk=${signatureOk} ihdrTag="${ihdrTag}" ihdrLength=${ihdrLength}). ` +
          "Re-generate the .b64 sidecar from a real image before building.",
      );
    }
  },
};

mkdirSync(outDir, { recursive: true });

for (const name of ASSETS) {
  const b64Path = join(srcDir, `${name}.b64`);
  const outPath = join(outDir, name);
  const b64 = readFileSync(b64Path, "utf8").trim();
  const bytes = Buffer.from(b64, "base64");

  const ext = name.slice(name.lastIndexOf(".")).toLowerCase();
  const validate = VALIDATORS[ext];
  if (!validate) {
    throw new Error(`decode-assets: no magic-byte validator registered for extension "${ext}" (asset "${name}").`);
  }
  validate(bytes, b64Path);

  writeFileSync(outPath, bytes);
  console.log(`decode-assets: wrote ${outPath} (${bytes.length} bytes)`);
}
