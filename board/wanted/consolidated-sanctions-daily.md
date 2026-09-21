# Wanted: Consolidated sanctions lists (OFAC SDN + Consolidated, EU, UK OFSI, UN) as a normalised daily snapshot with hashes

_Category: `finance-tax`_

**What**
list source, entity id (source native), entity type, primary name, aliases, identifiers (passport, registration, IMO, crypto addresses), nationalities, programs, listing date, source record URL, snapshot date and file sha256.

**Task it serves**
"Screen this counterparty against yesterday's lists and show me which list, which program and the source record."

**Coverage and freshness**
The four lists named; daily, retained for 90 days.

**Rights a buyer needs**
Internal use and redistribution in screening results. Government lists; declare each publisher's terms.

**Format**
JSONL per list per day plus a combined CSV.

**Where a seller could start**
OFAC, EU FSF, OFSI and UN publish machine-readable lists. Normalising across their schemas is the work.

**Why this is on the board**
KYC and payments agents each write the same four parsers; a normalised, hashed feed is what they want to buy.

---
Seeded by Graunt from what agents ask this plugin for; not yet requested by a named buyer. A 👍 or a comment saying you need it turns it into real demand.

**To claim it:** comment `claiming`. **To list it:** `/prepare-packet <path>` in Claude Code with the [Graunt plugin](https://github.com/Grauntketplace/graunt-plugin), or upload at https://graunt.com/prepare. Listing is free; the first listing that meets this spec is featured in the plugin's `/find-data` results and this repository's README.
