# Graunt seed packets

Build tooling for the first batch of **seed packets** on [Graunt](https://graunt.com): small, high-value reference datasets that
agents constantly need (holiday calendars, ISO code lists, SPDX licence identifiers and texts, IANA time zones, US states, world
countries, NAICS), each published as an *agent-ready packet* — CSV + JSON data, a machine-readable `manifest.json`, a `RIGHTS.md`
with the verbatim licence text and the obligations in plain words, a listing-ready `README.md`, and SHA-256 checksums.

Everything is generated from **pinned, hash-verified upstream packages** on PyPI and npm. The build never reads anything else, and
it fails loudly if a downloaded artifact or its LICENSE file no longer matches the pinned hash.

## Rebuild

```bash
python3 packets/build.py            # builds everything into dist/ (downloads ~30 MB into dist/.cache on first run)
python3 packets/build.py --list     # packet slugs
python3 packets/build.py --only iso-4217-currencies,us-federal-holidays-2025-2030
python3 packets/build.py --clean    # wipe dist/ (including the cache) first
```

Requirements: Python 3.11+ and network access to `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`. No third-party
packages need to be installed; the script downloads the pinned wheels and tarballs itself and reads their data files directly
(the `holidays` wheel is imported from the download directory together with its two runtime dependencies).

Environment variables: `GRAUNT_CACHE` (download directory), `GRAUNT_PACKET_VERSION` (defaults to today's UTC date, `YYYY.MM.DD`),
`SOURCE_DATE_EPOCH` (fixes `built_at` and zip timestamps for reproducible archives).

Output layout:

```
dist/
  INDEX.md, index.json          # table of packets with row counts, licences, zip sizes and hashes
  REJECTED.md                   # candidates rejected for rights/provenance reasons, and every judgment call
  packets/<slug>/               # data/ (CSV + JSON), manifest.json, README.md, RIGHTS.md, checksums.sha256
  packets/<slug>.zip            # the same directory zipped, plus <slug>.zip.sha256
```

Pins live in [`sources.json`](sources.json): package, version, exact artifact URL, artifact SHA-256, the path and SHA-256 of every
licence file inside the artifact, the upstream authority and registry publication date. To refresh a packet, bump the version and
hashes there (`pip download` / the npm registry give you the new artifact; recompute the licence-file hashes after reading the
licence again) and rebuild.

## Rights policy

These packets exist to demonstrate the rigour Graunt sells, so the rules are strict:

1. **The licence is established from the artifact itself** — the LICENSE file (or, failing that, the licence declaration inside
   the artifact's own metadata), never from a registry field or a project website alone. Its hash is pinned.
2. **Declare only what the licence text supports.** Rights are never upgraded: a licence that is silent on model training yields
   `training_allowed: "unspecified"`; a notice-retention licence yields `redistribution_allowed: "with-attribution"`; ODbL yields
   `"share-alike"` and `citation_required: true`.
3. **Upstream authority is named** in `manifest.json#sources[].upstream` even when it could not be fetched directly (ISO
   maintenance agencies via Debian iso-codes, the SPDX Project, IANA, GeoNames, the US Census Bureau), and the packet still
   declares only the licence of the artifact actually used.
4. **`RIGHTS.md` carries the verbatim licence text and copyright notice** from the artifact plus a plain-words statement of what
   the buyer must do. For LGPL data it states that redistribution is permitted with the licence and notice intact and that the
   packet is the data files, not a derived program.
5. **Anything with unclear rights or provenance is rejected and written up** in `dist/REJECTED.md`, together with every accepted
   source that needed a judgment call (empty registry licence fields, third-party data inside an MIT wrapper, missing LICENSE files).
6. **No personal data.** Data files contain public reference values only; where a licence notice reproduces contributor names
   (holidays `CONTRIBUTORS`, pycountry `COPYRIGHT.txt`) that is stated in the rights notes.

Declarations are the publisher's statements, not an audit and not legal advice.

## Packets

| slug | kind | what it is | source (pinned) | licence |
|---|---|---|---|---|
| `public-holidays-2025-2027-worldwide` | calendar | 55k dated rows: national + subdivision holidays for 250 countries/territories, with categories, observed/estimated flags and a coverage table | holidays 0.104 (PyPI) | MIT |
| `us-federal-holidays-2025-2030` | calendar | 73 rows: statutory and observed US federal holidays | holidays 0.104 | MIT |
| `financial-market-holidays-2025-2027` | calendar | 970 rows: trading holidays and half days for 25 markets (MIC-coded) | holidays 0.104 | MIT |
| `iso-3166-1-countries` | code-list | 249 country codes (alpha-2/3, numeric, names, flag) | pycountry 26.2.16 (Debian iso-codes) | LGPL-2.1-only |
| `iso-3166-2-subdivisions` | code-list | 5,046 subdivisions with type and parent | pycountry 26.2.16 | LGPL-2.1-only |
| `iso-3166-3-historic-countries` | code-list | 31 withdrawn country codes with successor notes | pycountry 26.2.16 | LGPL-2.1-only |
| `iso-4217-currencies` | code-list | 178 currency codes | pycountry 26.2.16 | LGPL-2.1-only |
| `iso-639-languages` | code-list | 7,923 ISO 639-3 languages (+639-1/-2 mappings) and 115 ISO 639-5 families | pycountry 26.2.16 | LGPL-2.1-only |
| `iso-15924-scripts` | code-list | 226 script codes | pycountry 26.2.16 | LGPL-2.1-only |
| `spdx-license-list-identifiers` | code-list | 727 SPDX identifiers (list 3.28.0) with name, URL, OSI and deprecated flags | spdx-license-list 6.12.0 + spdx-license-ids 3.0.24 (npm) | CC0-1.0 |
| `spdx-license-texts` | license-text | full canonical texts of the same 727 licences with per-text SHA-256 | spdx-license-list 6.12.0 | CC0-1.0 (texts © their stewards) |
| `iana-time-zones-current` | time-zones | 315 grouped zones (+418 identifiers, 272 abbreviations) with 2026 offsets, DST flags, links and countries from IANA 2026d | @vvo/tzdb 6.198.0 (npm) + tzdata 2026.4 (PyPI) | MIT (+ GeoNames CC BY 4.0 attribution) |
| `us-states-and-territories` | code-list | 59 rows: states, DC, territories, obsolete entities with FIPS, capitals, time zones | us 3.2.0 (PyPI) | BSD-3-Clause |
| `world-countries` | country-profiles | 250 countries: names, codes, capitals, currencies, dialling codes, languages, borders, area | world-countries 5.1.0 (npm) | ODbL-1.0 (share-alike) |
| `naics-2017-codes` | classification | 2,196 NAICS 2017 codes with titles, descriptions and hierarchy (superseded edition, clearly labelled) | naics 1.2.1 (npm; Census workbook inside) | MIT (US Government work) |

Rejected: `airport-codes` (npm) — declares ISC but redistributes OpenFlights data (ODbL/DbCL) and an unexplained OurAirports-style
CSV, no LICENSE file, 2015 snapshot. Details and all judgment calls: `dist/REJECTED.md` after a build.

## Manifest format

`manifest.json` follows the Graunt staging manifest `0.1` vocabulary (the same one `scripts/prepare_packet.py` writes): `family` /
`kind` / `sources` / `structure` / `evidence_refs` (INTEGRITY, STRUCTURE, FRESHNESS, RIGHTS, SAFETY, ORIGIN, DELIVERY_MATCH), rights
fields `license_family`, `redistribution_allowed`, `training_allowed`, `citation_required`, `personal_data`, provenance per source
(`name`, `url`, `version_or_edition`, `transformation`, `fetched_at`, plus package, artifact URL and SHA-256, licence-file hashes,
upstream authority and publication date), `freshness`, `limitations`, `safety_notes` and a `citation_template` that names the
primary file's SHA-256.

## Conventions

- CSV: UTF-8, header row, RFC 4180 quoting, LF line endings, `true`/`false` booleans, empty string for null, `;`-separated lists,
  `key=value;key=value` maps, stable sort order (documented per table).
- JSON: array of objects with the same records and field order, one record per line; lists and maps are arrays and objects.
- Row counts in the manifest are verified by re-reading the written files; every hash in `checksums.sha256` is re-verified; the zip
  is checked to contain exactly the packet directory.
- Every built directory is a complete staging bundle in the sense of `scripts/prepare_packet.py`: `python3 scripts/prepare_packet.py validate dist/packets/<slug>`
  prints `READY` for each, so a bundle can be uploaded at graunt.com/prepare as-is. `RIGHTS.md` follows that script's section layout
  (who holds the rights, grant, what buyers may do, sources table with propagating obligations, personal data, attribution line,
  licence texts) and `README.md` its listing layout (what you get, agent tasks, coverage, schema, limitations, freshness, provenance,
  rights, citation).
