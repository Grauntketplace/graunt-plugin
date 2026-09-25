# Wanted: LEI ↔ national company-registry ID ↔ ticker ↔ SEC CIK crosswalk, monthly

_Category: `finance-tax`_

**What**
LEI, legal name, jurisdiction, registry authority id and registration number, ISIN/ticker/MIC where mapped, SEC CIK where applicable, parent LEI, status, mapping source and confidence, snapshot date.

**Task it serves**
"Resolve 'Siemens Energy AG' to its LEI, German registry number and Frankfurt ticker, and tell me which mappings are authoritative."

**Coverage and freshness**
All active LEIs (about 2.8 million); monthly.

**Rights a buyer needs**
GLEIF data is CC0; SEC data public domain; ISIN-ticker mappings vary by source, declare each. Redistribution needed.

**Format**
Parquet plus CSV sample.

**Where a seller could start**
GLEIF Golden Copy and mapping files (CC0), SEC company tickers JSON (public domain), OpenFIGI (check terms).

**Why this is on the board**
Entity resolution is the first step of every finance agent workflow and the join is nowhere in one place with declared rights.

---
Seeded by Graunt from what agents ask this plugin for; not yet requested by a named buyer. A 👍 or a comment saying you need it turns it into real demand.

**To claim it:** comment `claiming`. **To list it:** `/prepare-packet <path>` in Claude Code with the [Graunt plugin](https://github.com/Grauntketplace/graunt-plugin), or upload at https://graunt.com/prepare. Listing is free; the first listing that meets this spec is featured in the plugin's `/find-data` results and this repository's README.
