---
name: verify-packet
description: Check a Graunt packet before relying on it — pull its file inventory and content hashes from the catalog, compare against local files if the user has them, and restate the declared rights.
argument-hint: <packet id or URL> [path to downloaded files]
---

Verify the packet named in `$ARGUMENTS` before the user relies on it.

## 1. Pull the inventory

From the `graunt` MCP server:

- Call `fetch` with the packet id for title, description, commercial terms and
  the public URL.
- Call `get_bundle_manifest` with the same id for the file inventory: each
  delivered file's role, format, size and content hash, plus the counts that
  name the population each one counted. If that tool is not exposed by your
  client, say so and work from `fetch` alone.

## 2. Compare against local files, if any

If the user gave a path to files they downloaded, hash each one and match by
path or name:

```bash
sha256sum <file>          # Linux
shasum -a 256 <file>      # macOS
```

Report per file: **match**, **mismatch** (both hashes, first 12 chars), or
**not in manifest**. A mismatch means the bytes they have are not the bytes
the seller published; say that plainly and stop short of speculating why.

## 3. Restate the rights

From the `fetch` result, in one short paragraph: license family, whether
redistribution and training are permitted, whether citation is required, and
whether personal data is declared. Treat a missing or ambiguous field as
restrictive and say which field was unclear.

## 4. Give a verdict

One of:

- "Inventory verified, N files match, rights permit <the user's stated use>."
- "Inventory verified, but rights do not clearly permit <use> because <field>."
- "Could not verify: <what was missing>."

Include the packet id, the manifest's snapshot or version identifier, and the
hash of any file the user will actually use, so the verdict is citable.
