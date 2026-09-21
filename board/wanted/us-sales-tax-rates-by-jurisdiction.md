# Wanted: US sales and use tax rates by jurisdiction, monthly, with effective dates and source citations

_Category: `finance-tax`_

**What**
Jurisdiction id, name, level (state/county/city/district), rate component, combined rate, effective date, end date, source document URL, snapshot date. Optional: ZIP+4 or geocode boundary keys.

**Task it serves**
"What is the combined rate for this address as of today, and what changed since last month?"

**Coverage and freshness**
All US states with a sales tax; monthly, with a diff file.

**Rights a buyer needs**
Internal use and redistribution to end customers (invoicing). Attribution fine.

**Format**
CSV plus a monthly diff CSV.

**Where a seller could start**
State departments of revenue publish rate tables and change notices (public records). Compiling them is the work.

**Why this is on the board**
Every invoicing or e-commerce agent needs it; most pay for an API when a monthly table would do.

---
Seeded by Graunt from what agents ask this plugin for; not yet requested by a named buyer. A 👍 or a comment saying you need it turns it into real demand.

**To claim it:** comment `claiming`. **To list it:** `/prepare-packet <path>` in Claude Code with the [Graunt plugin](https://github.com/Grauntketplace/graunt-plugin), or upload at https://graunt.com/prepare. Listing is free; the first listing that meets this spec is featured in the plugin's `/find-data` results and this repository's README.
