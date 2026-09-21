# Wanted: eCFR section-level text snapshots with stable citations and content hashes (Titles 12, 17, 21, 26, 29, 40, 45 first)

_Category: `legal-regulatory`_

**What**
title, part, section, heading, full text (plain), effective date, amendment history reference, eCFR URL, sha256 of the text, snapshot date. A monthly diff listing sections added, amended or removed.

**Task it serves**
"Quote 21 CFR 11.10(a) exactly, as of 1 September 2026, with a citation I can verify."

**Coverage and freshness**
Named titles first, all 50 eventually; monthly; point-in-time retrieval by snapshot date.

**Rights a buyer needs**
US federal government work: public domain. Redistribution and training allowed; citation of the packet version and hash expected.

**Format**
JSONL per title plus CSV index.

**Where a seller could start**
eCFR bulk XML and API (public domain). The value is the section-level normalisation, hashes and diffs.

**Why this is on the board**
Compliance agents paraphrase from memory and get clause numbers wrong; exact text with a hash ends that.

---
Seeded by Graunt from what agents ask this plugin for; not yet requested by a named buyer. A 👍 or a comment saying you need it turns it into real demand.

**To claim it:** comment `claiming`. **To list it:** `/prepare-packet <path>` in Claude Code with the [Graunt plugin](https://github.com/Grauntketplace/graunt-plugin), or upload at https://graunt.com/prepare. Listing is free; the first listing that meets this spec is featured in the plugin's `/find-data` results and this repository's README.
