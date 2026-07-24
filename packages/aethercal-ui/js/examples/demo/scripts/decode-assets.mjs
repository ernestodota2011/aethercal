#!/usr/bin/env node
/**
 * Decodes the base64 sidecars under assets-src/ into real binary files under public/ (E-01,
 * AetherCal-13 Ola 1) — a build/dev prerequisite, not a Vite plugin, so it stays trivial.
 *
 * Why this exists: the source of truth for these two screenshots has to be something every
 * publishing path can carry byte-for-byte. Real binary files are ideal for a browser (cacheable,
 * lazy-loadable, no HTML bloat) but this branch is published through the GitHub Contents API via
 * MCP tools, which take file `content` as literal text — verified empirically (a probe file's
 * base64 string came back stored verbatim, not decoded) to NOT carry raw binary without
 * corruption. Base64 text sidecars are the one representation that survives every path: they are
 * plain text (safe for MCP, `git diff`, and PR review) AND they decode back to the exact original
 * bytes. This script is the one place that conversion happens, so `public/*.webp` is always a
 * reproducible BUILD ARTIFACT (gitignored — see .gitignore) rather than something committed twice
 * in two different shapes that could drift apart.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const srcDir = join(root, "assets-src");
const outDir = join(root, "public");

/** Every asset this demo needs decoded. Add a line here, not a bespoke script, for a new one. */
const ASSETS = ["screenshot-calendar.webp", "screenshot-live-booking.webp"];

mkdirSync(outDir, { recursive: true });

for (const name of ASSETS) {
  const b64Path = join(srcDir, `${name}.b64`);
  const outPath = join(outDir, name);
  const b64 = readFileSync(b64Path, "utf8").trim();
  const bytes = Buffer.from(b64, "base64");

  // Fail loudly rather than silently ship a corrupt/truncated image: every asset here is a WebP,
  // so its first 12 bytes are the RIFF container header with the four-byte "WEBP" fourcc at
  // offset 8 (https://developers.google.com/speed/webp/docs/riff_container). Checking only that
  // header (Crisol gate round 5, medium confidence) would still pass a TRUNCATED file, since the
  // magic bytes are the first thing written — so also read the RIFF chunk size at bytes[4..8)
  // (little-endian, the byte count of everything AFTER those 8 header bytes) and require it to
  // match what actually decoded. A sidecar cut off mid-base64 fails this even though its header
  // is intact.
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

  writeFileSync(outPath, bytes);
  console.log(`decode-assets: wrote ${outPath} (${bytes.length} bytes)`);
}
