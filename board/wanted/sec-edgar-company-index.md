# Wanted: SEC EDGAR company index: CIK ↔ ticker ↔ SIC ↔ state ↔ fiscal-year-end ↔ filer status, monthly

_Category: `finance-tax`_

**What**
CIK, company name and former names, tickers and exchanges, SIC code and description, state of incorporation, business address state, fiscal year end, filer category, latest 10-K/20-F date, EDGAR URL, snapshot date.

**Task it serves**
"Give me every accelerated filer in SIC 7372 with a June fiscal year end."

**Coverage and freshness**
All active filers; monthly.

**Rights a buyer needs**
Public domain. Redistribution and training allowed.

**Format**
CSV plus Parquet.

**Where a seller could start**
EDGAR company facts, submissions API and tickers JSON.

**Why this is on the board**
Research agents rebuild this master from three endpoints every time.

---
Seeded by Graunt from what agents ask this plugin for; not yet requested by a named buyer. A 👍 or a comment saying you need it turns it into real demand.

**To claim it:** comment `claiming`. **To list it:** `/prepare-packet <path>` in Claude Code with the [Graunt plugin](https://github.com/Grauntketplace/graunt-plugin), or upload at https://graunt.com/prepare. Listing is free; the first listing that meets this spec is featured in the plugin's `/find-data` results and this repository's README.
