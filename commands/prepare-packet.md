---
name: prepare-packet
description: Turn a file or folder of data into a review-ready Graunt packet — inventory, hashes, schema, an honest rights declaration and listing text — then hand off to graunt.com/prepare.
argument-hint: <path to a file or folder> [--title "Listing title"]
---

Turn `$ARGUMENTS` into a Graunt packet staging bundle and get it ready to upload.
Read the `publishing-agent-ready-data` skill first if it is not already loaded.

## 1. Ask before you build (two minutes, not twenty)

You cannot declare rights the user does not hold, so establish these before
writing a single manifest field. Ask only what the files do not already answer:

1. **Where did this come from?** Original work, a public source, a licensed
   source, scraped? For each source: its license (SPDX id if known) and URL.
2. **What may buyers do?** Use internally; redistribute to their own users;
   train models on it. Attribution required?
3. **Any personal data?** Names, contacts, identifiers, precise locations.
   The script flags suspicious column names; the user confirms.
4. **What is it for?** The two or three concrete tasks an agent would use it
   for, the coverage (time, geography), and the date it reflects.
5. **Price idea?** Free, one-time, or plan. Free is a fine answer for code
   lists and first listings; say so.

"I don't know" on rights means restrictive: `redistribution_allowed: false`,
`training_allowed: "unspecified"`, and say why in `rights.notes`.

## 2. Stage the files

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prepare_packet.py" init <path> --out ./graunt-packet --title "<listing title>"
```

This copies the files into `graunt-packet/data/`, computes SHA-256 for each,
infers CSV/JSON schemas and row counts, flags personal-data-looking columns,
and writes `manifest.json`, `RIGHTS.md`, `README.md` and `checksums.sha256`
with every field you must supply marked `REQUIRED`.

## 3. Fill the bundle from the answers

- `manifest.json`: family, kind, summary, intended uses, coverage, sources,
  rights, freshness, limitations, and a description for every column.
  Allowed rights values: `redistribution_allowed` ∈ true | false |
  "with-attribution" | "share-alike"; `training_allowed` ∈ true | false |
  "unspecified"; `personal_data` ∈ "none" | "contains-personal-data" |
  "pseudonymised" | "aggregated-only".
- `RIGHTS.md`: who holds the rights, the grant, the source table with the
  obligations that propagate, personal data, attribution line, license texts.
- `README.md`: this becomes the listing an agent reads. Lead with what it is
  and the single most useful task. Coverage, limitations, as-of date, citation.

Write plainly. An agent deciding whether to buy needs coverage and limitations
more than adjectives.

## 4. Validate, then zip

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prepare_packet.py" checksums ./graunt-packet   # after editing README/RIGHTS
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prepare_packet.py" validate ./graunt-packet
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/prepare_packet.py" zip ./graunt-packet
```

`validate` refuses to pass while a `REQUIRED` placeholder remains, a hash or
row count disagrees with the files, or a rights value is one Graunt cannot
interpret. Fix and re-run until it prints `READY`.

## 5. Hand off

Tell the user, in this order:

1. Upload the bundle (or the zip) at **https://graunt.com/prepare**. Graunt
   drafts the packet and listing from the files; they confirm rights, set the
   price and publish. Drafts are private until review passes.
2. If the packet matches an open request on the board
   (https://github.com/Grauntketplace/graunt-plugin/issues?q=is%3Aissue+is%3Aopen+label%3Awanted-packet),
   comment there with the listing link once it is live; fulfilled requests are
   featured in the plugin.
3. What they declared, in one paragraph, so they can correct you before it is
   public.

Do not upload anything yourself and do not post to the board on the user's
behalf. Those are theirs to do.
