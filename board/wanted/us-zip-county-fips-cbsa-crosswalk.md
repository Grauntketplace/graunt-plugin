# Wanted: US ZIP ↔ county ↔ FIPS ↔ CBSA ↔ congressional district crosswalk with vintage and allocation ratios

_Category: `geo-reference`_

**What**
ZIP, ZCTA, county FIPS and name, state, CBSA code and name, congressional district (with Congress number), residential/business/total allocation ratios for many-to-many cases, vintage, source.

**Task it serves**
"Map these 40,000 customer ZIPs to counties and CBSAs, handling ZIPs that span counties."

**Coverage and freshness**
All US ZIPs; quarterly with HUD releases, annual for districts and CBSAs.

**Rights a buyer needs**
Redistribution in products. HUD and Census sources are public domain; declare.

**Format**
CSV plus Parquet.

**Where a seller could start**
HUD USPS ZIP crosswalk, Census ZCTA relationship files, OMB CBSA delineations.

**Why this is on the board**
Analytics agents redo this join badly every week.

---
Seeded by Graunt from what agents ask this plugin for; not yet requested by a named buyer. A 👍 or a comment saying you need it turns it into real demand.

**To claim it:** comment `claiming`. **To list it:** `/prepare-packet <path>` in Claude Code with the [Graunt plugin](https://github.com/Grauntketplace/graunt-plugin), or upload at https://graunt.com/prepare. Listing is free; the first listing that meets this spec is featured in the plugin's `/find-data` results and this repository's README.
