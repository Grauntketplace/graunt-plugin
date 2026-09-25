#!/usr/bin/env python3
"""Build Graunt seed packets from pinned upstream packages.

Standard library only. Everything else is downloaded at build time from the
pins in packets/sources.json (PyPI wheels and npm tarballs), verified against
the pinned SHA-256, extracted, normalised to CSV + JSON, hashed, described in a
manifest, and zipped under dist/packets/.

Usage:  python3 packets/build.py [--only slug[,slug]] [--keep-cache]

Environment:
  GRAUNT_CACHE            directory for downloaded artifacts (default dist/.cache)
  GRAUNT_PACKET_VERSION   packet version string (default: today's UTC date, YYYY.MM.DD)
  SOURCE_DATE_EPOCH       fixes built_at and zip timestamps for reproducible builds
"""
from __future__ import annotations

import argparse
import ast
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request
import warnings
import zipfile
import zoneinfo
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKETS_DIR = ROOT / "packets"
DIST = ROOT / "dist"
OUT = DIST / "packets"
CACHE = Path(os.environ.get("GRAUNT_CACHE") or (DIST / ".cache"))
SOURCES_FILE = PACKETS_DIR / "sources.json"
PINS = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
SOURCES = PINS["sources"]
REJECTED = PINS["rejected"]

_epoch = os.environ.get("SOURCE_DATE_EPOCH")
NOW = dt.datetime.fromtimestamp(int(_epoch), dt.timezone.utc) if _epoch else dt.datetime.now(dt.timezone.utc)
BUILT_AT = NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z")
VERSION = os.environ.get("GRAUNT_PACKET_VERSION") or NOW.strftime("%Y.%m.%d")
MANIFEST_SCHEMA = "0.1"
FAMILY = "reference-data"
GRAUNT_URL = "https://graunt.com"
USER_AGENT = "graunt-seed-packets-build/0.1 (+https://github.com/Grauntketplace/graunt-plugin)"

warnings.simplefilter("ignore")


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Download, verify, extract
# --------------------------------------------------------------------------- #
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_source(sid: str) -> Path:
    """Download the pinned artifact (if not cached), verify sha256, extract, verify license files.
    Returns the extraction root. Records fetched_at in SOURCES[sid]['fetched_at']."""
    s = SOURCES[sid]
    art_dir = CACHE / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    art = art_dir / s["filename"]
    stamp = art.with_suffix(art.suffix + ".fetched_at")
    if art.exists() and sha256_file(art) == s["sha256"] and stamp.exists():
        fetched_at = stamp.read_text().strip()
    else:
        log(f"  downloading {s['package']} {s['version']} <- {s['url']}")
        req = urllib.request.Request(s["url"], headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
        got = sha256_bytes(data)
        if got != s["sha256"]:
            raise SystemExit(f"sha256 mismatch for {sid}: expected {s['sha256']}, got {got}")
        art.write_bytes(data)
        fetched_at = BUILT_AT if _epoch else dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        stamp.write_text(fetched_at)
    s["fetched_at"] = fetched_at
    s["artifact_path"] = str(art)

    root = CACHE / "extracted" / sid
    marker = root / ".extracted.sha256"
    if not (marker.exists() and marker.read_text().strip() == s["sha256"]):
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        if art.suffix == ".whl":
            with zipfile.ZipFile(art) as z:
                z.extractall(root)
        elif art.name.endswith(".tgz") or art.name.endswith(".tar.gz"):
            with tarfile.open(art, "r:gz") as t:
                members = []
                for m in t.getmembers():
                    # npm tarballs are rooted at package/; strip it and refuse path escapes
                    parts = Path(m.name).parts
                    if not parts or parts[0] != "package":
                        continue
                    rel = Path(*parts[1:]) if len(parts) > 1 else None
                    if rel is None or ".." in rel.parts:
                        continue
                    m.name = str(rel)
                    members.append(m)
                t.extractall(root, members=members)
        else:
            raise SystemExit(f"unknown artifact type: {art}")
        marker.write_text(s["sha256"])
    for lf in s.get("license_files", []):
        p = root / lf["path"]
        if not p.exists():
            raise SystemExit(f"{sid}: pinned license file missing: {lf['path']}")
        got = sha256_file(p)
        if got != lf["sha256"]:
            raise SystemExit(f"{sid}: license file {lf['path']} changed (sha256 {got} != pinned {lf['sha256']}); re-review rights before building")
    s["extracted_root"] = str(root)
    return root


def license_text(sid: str, idx: int = 0) -> str:
    s = SOURCES[sid]
    p = Path(s["extracted_root"]) / s["license_files"][idx]["path"]
    return p.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Serialisation helpers
# --------------------------------------------------------------------------- #
def csv_cell(v):
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (list, tuple)):
        return ";".join(csv_cell(x) for x in v)
    if isinstance(v, dict):
        return ";".join(f"{k}={csv_cell(val)}" for k, val in v.items())
    return str(v)


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([csv_cell(r.get(c)) for c in columns])
    return len(rows)


def write_json(path: Path, columns: list[str], rows: list[dict]) -> int:
    """JSON array of objects, one record per line, UTF-8, key order = column order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("[\n")
        for i, r in enumerate(rows):
            obj = {c: r.get(c) for c in columns}
            f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
            f.write(",\n" if i < len(rows) - 1 else "\n")
        f.write("]\n")
    return len(rows)


def read_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as f:
        n = sum(1 for _ in csv.reader(f))
    return n - 1


def read_json_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, list)
    return len(data)


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n} B"


# --------------------------------------------------------------------------- #
# Packet model
# --------------------------------------------------------------------------- #
@dataclass
class Col:
    name: str
    type: str
    description: str


@dataclass
class Table:
    """One logical table written as CSV (and a JSON twin unless json=False)."""
    stem: str                      # data/<stem>.csv, data/<stem>.json
    columns: list[Col]
    rows: list[dict]
    description: str
    role: str = "primary"          # primary | documentation
    json_twin: bool = True


@dataclass
class ExtraFile:
    path: str                      # relative path inside the packet
    role: str
    format: str
    description: str
    content: bytes
    rows: int | None = None


@dataclass
class LicenseDoc:
    heading: str
    text: str


@dataclass
class Packet:
    slug: str
    title: str
    kind: str
    summary: str
    intended_uses: list[str]
    not_intended_for: list[str]
    coverage: dict
    source_ids: list[str]
    rights: dict
    tables: list[Table]
    freshness: dict
    limitations: list[str]
    agent_tasks: list[str]
    citation_subject: str
    rights_plain: list[str]
    attribution_notice: str
    license_docs: list[LicenseDoc]
    extra_files: list[ExtraFile] = field(default_factory=list)
    readme_extra: list[tuple[str, str]] = field(default_factory=list)
    safety_notes: list[str] = field(default_factory=lambda: ["No personal data: the data files contain public reference values only."])
    source_notes: list[str] = field(default_factory=list)
    transformations: dict = field(default_factory=dict)   # source id -> what the build did to it
    rights_holder: str = ("The upstream authors named in the licence notices below hold the rights in the source data; the packet builder "
                          "holds no additional rights in the data and claims none. The build only extracted, expanded or re-serialised the source files.")


PROPAGATING_OBLIGATIONS = {
    "MIT": "keep the copyright and permission notice with every copy or substantial portion",
    "BSD-3-Clause": "keep the copyright notice, the conditions and the disclaimer; no endorsement using the licensor's name",
    "LGPL-2.1-only": "keep the LGPL-2.1 text and the copyright/attribution notices; modified copies of the data stay under LGPL-2.1",
    "CC0-1.0": "none (public-domain dedication); citing the SPDX list version is good practice",
    "ODbL-1.0": "attribution notice on public use; adapted databases must be offered under ODbL-1.0; no DRM without an open parallel copy",
    "Apache-2.0": "none propagate to this packet: no file from the package is redistributed, only computed facts",
    "Apache-2.0 OR BSD-3-Clause": "none propagate: runtime dependency only, nothing copied",
}
DEFAULT_TRANSFORMATION = "extracted the data files and re-serialised them to CSV and JSON without changing values; derived columns are documented in the schema"


# --------------------------------------------------------------------------- #
# Shared rights vocabularies
# --------------------------------------------------------------------------- #
def rights_mit(notes: str) -> dict:
    return {
        "license_family": "MIT",
        "redistribution_allowed": "with-attribution",
        "training_allowed": "unspecified",
        "citation_required": False,
        "personal_data": "none",
        "notes": notes,
    }


MIT_PLAIN = [
    "You may use, copy, modify, merge, publish, distribute, sublicense and sell copies of the data, free of charge.",
    "Every copy or substantial portion you redistribute must carry the copyright notice and the permission notice reproduced below (RIGHTS.md satisfies this if shipped with the data).",
    "The data is provided as-is, without warranty; the authors are not liable for how you use it.",
    "The MIT text does not mention model training; this packet therefore declares training as unspecified rather than permitted.",
]

HOLIDAY_YEARS = [2025, 2026, 2027]


def load_holidays_module():
    for sid in ("holidays", "python-dateutil", "six"):
        root = fetch_source(sid)
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    import holidays  # noqa: E402  (downloaded at build time)
    return holidays


def humanize_class_name(name: str) -> str:
    # NewYorkStockExchange -> New York Stock Exchange ; SIXSwissExchange -> SIX Swiss Exchange
    return re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", name)


def _holiday_records(hmod, factory, entity, subdiv, categories, lang):
    """Return {(date, name): {'categories': set, 'observed': bool}} for one entity/subdivision."""
    recs: dict = {}
    for cat in categories:
        kw = dict(years=HOLIDAY_YEARS, categories=(cat,), language=lang)
        if subdiv is not None:
            kw["subdiv"] = subdiv
        h_obs = factory(entity, observed=True, **kw)
        h_plain = factory(entity, observed=False, **kw)
        base = {(d, n) for d in h_plain for n in h_plain.get_list(d)}
        for d in h_obs:
            for n in h_obs.get_list(d):
                r = recs.setdefault((d, n), {"categories": set(), "observed": (d, n) not in base})
                r["categories"].add(cat)
    return recs


def iso3166_country_names() -> dict:
    root = fetch_source("pycountry")
    data = json.loads((root / "pycountry/databases/iso3166-1.json").read_text(encoding="utf-8"))["3166-1"]
    return {r["alpha_2"]: r["name"] for r in data}


def build_holidays_worldwide() -> Packet:
    hmod = load_holidays_module()
    from holidays.registry import COUNTRIES
    names = iso3166_country_names()
    code2cls = {v[1]: getattr(hmod, v[0]) for v in COUNTRIES.values()}
    rows, coverage = [], []
    for code in sorted(code2cls):
        cls = code2cls[code]
        subdivs = sorted(cls.subdivisions)
        cats = sorted(cls.supported_categories) or ["public"]
        langs = list(getattr(cls, "supported_languages", ()) or ())
        lang = "en_US" if "en_US" in langs else None
        national = _holiday_records(hmod, hmod.country_holidays, code, None, cats, lang)
        national_keys = set(national)
        per_year = {y: 0 for y in HOLIDAY_YEARS}
        total = 0
        for subdiv in [None] + subdivs:
            recs = national if subdiv is None else _holiday_records(hmod, hmod.country_holidays, code, subdiv, cats, lang)
            for (d, n) in sorted(recs, key=lambda k: (k[0], k[1])):
                r = recs[(d, n)]
                rows.append({
                    "country_iso2": code,
                    "subdivision": subdiv or "",
                    "date": d.isoformat(),
                    "name": n,
                    "categories": sorted(r["categories"]),
                    "observed": r["observed"],
                    "estimated": "(estimated)" in n,
                    "subdivision_specific": (subdiv is not None) and ((d, n) not in national_keys),
                    "name_language": lang or "",
                })
                total += 1
                if subdiv is None:
                    per_year[d.year] += 1
        coverage.append({
            "country_iso2": code,
            "country_name": names.get(code, ""),
            "subdivisions_count": len(subdivs),
            "subdivisions": subdivs,
            "categories": cats,
            "languages_supported": langs,
            "name_language": lang or "",
            "start_year": getattr(cls, "start_year", None),
            "end_year": getattr(cls, "end_year", None),
            "national_rows_2025": per_year[2025],
            "national_rows_2026": per_year[2026],
            "national_rows_2027": per_year[2027],
            "rows_total": total,
        })
    rows.sort(key=lambda r: (r["country_iso2"], r["subdivision"], r["date"], r["name"]))
    n_countries = len(coverage)
    n_subdivs = sum(c["subdivisions_count"] for c in coverage)
    unlocalised = sum(1 for c in coverage if not c["name_language"])
    zero = [c["country_iso2"] for c in coverage if not c["rows_total"]]
    n_with_rows = n_countries - len(zero)
    short_end = [f"{c['country_iso2']} (through {c['end_year']})" for c in coverage if c["end_year"] and c["end_year"] < max(HOLIDAY_YEARS)]
    hol_cols = [
        Col("country_iso2", "string", "ISO 3166-1 alpha-2 code of the country or territory"),
        Col("subdivision", "string", "Subdivision code as used by the holidays library (normally the ISO 3166-2 suffix, e.g. CA for US-CA); empty for the national calendar"),
        Col("date", "date", "Calendar date, YYYY-MM-DD"),
        Col("name", "string", "Holiday name (English where the library provides an en_US translation; see name_language)"),
        Col("categories", "list", "Holiday categories this entry belongs to (public, bank, school, government, optional, half_day, workday, ...); ';'-separated in CSV"),
        Col("observed", "boolean", "true when this row is a substitute/observed date generated because the holiday fell on a non-working day"),
        Col("estimated", "boolean", "true when the library marks the date as an estimate (lunar/religious calendars not yet officially announced)"),
        Col("subdivision_specific", "boolean", "true when the (date, name) pair does not appear in the national calendar of the same country"),
        Col("name_language", "string", "Language of the name: en_US when the library's English translation was requested; empty when the entity has no translations and its names are English by construction"),
    ]
    cov_cols = [
        Col("country_iso2", "string", "ISO 3166-1 alpha-2 code"),
        Col("country_name", "string", "ISO 3166-1 short name (from the iso-3166-1-countries packet source, pycountry 26.2.16)"),
        Col("subdivisions_count", "integer", "Number of subdivisions the library models for this country"),
        Col("subdivisions", "list", "Subdivision codes covered; ';'-separated in CSV"),
        Col("categories", "list", "Holiday categories supported for this country"),
        Col("languages_supported", "list", "Name translations available in the library"),
        Col("name_language", "string", "Language used for names in holidays.csv"),
        Col("start_year", "integer", "First year the library models for this country"),
        Col("end_year", "integer", "Last year the library models for this country"),
        Col("national_rows_2025", "integer", "National-calendar rows for 2025 (all categories)"),
        Col("national_rows_2026", "integer", "National-calendar rows for 2026 (all categories)"),
        Col("national_rows_2027", "integer", "National-calendar rows for 2027 (all categories)"),
        Col("rows_total", "integer", "All rows for this country in holidays.csv (national + every subdivision)"),
    ]
    hv = SOURCES["holidays"]["version"]
    return Packet(
        slug="public-holidays-2025-2027-worldwide",
        title="Public holidays 2025–2027, worldwide (national and subdivision calendars)",
        kind="calendar",
        summary=(f"Every public, bank, school, government and optional holiday that the holidays library (Vacanza Team, v{hv}) models for "
                 f"{n_countries} countries and territories ({n_with_rows} with holidays in this period) and {n_subdivs} subdivisions, expanded for calendar years 2025, 2026 and 2027 into one flat "
                 f"table of {len(rows):,} dated rows. Each row carries the ISO country code, the subdivision code, the date, an English name, the holiday "
                 "categories, and flags for observed (substitute) dates, estimated dates and subdivision-specific entries. A companion coverage table "
                 "lists, per country, which subdivisions, categories and languages are modelled and how many rows exist per year."),
        intended_uses=["Business-day and SLA calculations by country or subdivision", "Scheduling and deadline logic that must skip public holidays",
                       "Validating or enriching dates in ETL pipelines", "Answering 'is <date> a public holiday in <place>' without recalling from memory"],
        not_intended_for=["Legal determinations of statutory entitlements (consult the official gazette)", "Religious observance times within a day",
                          "Years outside 2025–2027 (rebuild with different HOLIDAY_YEARS, or use the library directly)"],
        coverage={"temporal": "2025-01-01 to 2027-12-31", "geographic": f"{n_countries} ISO 3166-1 countries/territories modelled, {n_with_rows} with rows ({', '.join(zero)} have no holidays defined); {n_subdivs} subdivisions", "row_count": len(rows),
                  "entities": n_countries, "entities_with_rows": n_with_rows, "subdivisions": n_subdivs},
        source_ids=["holidays", "pycountry"],
        rights=rights_mit("MIT (Vacanza Team and contributors). The calendar rows are generated output of the MIT-licensed holidays library; keep the copyright and permission notice with redistributed copies. "
                          "RIGHTS.md reproduces the upstream CONTRIBUTORS list because the copyright notice refers to it."),
        tables=[Table("holidays", hol_cols, rows, "Dated holiday rows, sorted by country, subdivision, date, name"),
                Table("coverage", cov_cols, coverage, "Per-country coverage: subdivisions, categories, languages, year range and row counts", role="documentation")],
        freshness={"as_of": SOURCES["holidays"]["published"][:10],
                   "refresh_cadence": "holidays publishes roughly monthly; rebuild this packet with each release (pin bump in packets/sources.json)",
                   "known_staleness_risks": "Government decrees issued after the library release (one-off holidays, moved dates, newly declared days) are not reflected. "
                                            "Lunar/religious holidays for later years are estimates until officially announced (see the estimated flag)."},
        limitations=[
            "Holiday rules are as encoded by the holidays project from official sources; they are not the official gazettes themselves.",
            f"{len(zero)} registered entities have no holidays defined in the library and therefore no rows: {', '.join(zero)}. Entities whose modelled range ends before 2027 have no rows for later years: {', '.join(short_end) or 'none'}. See coverage.csv (start_year, end_year, per-year counts).",
            f"Names are English (en_US translation) for {n_countries - unlocalised} entities; {unlocalised} entities have no translation files and use the library's built-in English names (name_language is empty for them).",
            "Subdivision codes follow the library's conventions (usually the ISO 3166-2 suffix); a few entities use non-ISO codes. The coverage table lists the exact codes.",
            "Category semantics (public, bank, school, government, optional, half_day, workday, ...) follow the library's documentation; 'workday' marks days that are working days despite falling on a weekend.",
            "A subdivision row set is the complete calendar for that subdivision (national holidays included); use subdivision_specific to isolate local additions.",
            "The observed flag is derived by diffing the library's output with and without observed-date rules; it does not distinguish statutory substitute days from customary ones.",
            "No half-day times, no information on which employers are bound, and no company-specific closures.",
        ],
        agent_tasks=["Compute a business-day calendar for FR (or DE-BY, US-CA, IN-KA) for 2026 by excluding rows where categories contains 'public'",
                     "Check whether 2026-05-25 is a public holiday in the United Kingdom and in each of its constituent countries",
                     "List every 2027 holiday still flagged estimated so a scheduler can re-check them later",
                     "Add days-until-next-holiday features to a forecasting dataset keyed on country and date"],
        citation_subject=f"holidays {hv} (Vacanza Team, MIT), expanded for 2025–2027",
        rights_plain=MIT_PLAIN,
        attribution_notice="Generated from the holidays library (https://github.com/vacanza/holidays), Copyright (c) Vacanza Team and individual contributors; Copyright (c) dr-prodigy 2017-2023; Copyright (c) ryanss 2014-2017. MIT License.",
        license_docs=[LicenseDoc(f"holidays {hv} — LICENSE (MIT)", license_text("holidays", 0)),
                      LicenseDoc(f"holidays {hv} — CONTRIBUTORS (referenced by the copyright notice)", license_text("holidays", 1))],
        source_notes=["pycountry 26.2.16 (LGPL-2.1-only) is used only to attach ISO short names in coverage.csv; those 250 name strings are the only content taken from it."],
        transformations={"holidays": "imported the library and generated every entity's calendar for 2025-2027 per category, with and without observed-date rules, then merged into one row per (entity, subdivision, date, name)",
                         "pycountry": "read iso3166-1.json for the 250 English short names used in coverage.csv"},
    )


def build_us_federal() -> Packet:
    hmod = load_holidays_module()
    years = list(range(2025, 2031))
    h = hmod.country_holidays("US", years=years, language="en_US", observed=True)
    hp = hmod.country_holidays("US", years=years, language="en_US", observed=False)
    base = {(d, n) for d in hp for n in hp.get_list(d)}
    rows = []
    for d in sorted(h):
        for n in h.get_list(d):
            rows.append({"date": d.isoformat(), "weekday": d.strftime("%A"), "year": d.year,
                         "holiday": n.replace(" (observed)", ""), "name": n, "observed": (d, n) not in base})
    rows.sort(key=lambda r: (r["date"], r["name"]))
    cols = [Col("date", "date", "Calendar date, YYYY-MM-DD"), Col("weekday", "string", "Day of week"), Col("year", "integer", "Calendar year"),
            Col("holiday", "string", "Federal holiday name without the observed suffix (join key between an actual date and its observed date)"),
            Col("name", "string", "Name as generated, including ' (observed)' for substitute days"),
            Col("observed", "boolean", "true when federal employees observe the holiday on this substitute date because the statutory date falls on a weekend")]
    hv = SOURCES["holidays"]["version"]
    return Packet(
        slug="us-federal-holidays-2025-2030",
        title="US federal holidays 2025–2030 (statutory and observed dates)",
        kind="calendar",
        summary=(f"The eleven United States federal holidays (5 U.S.C. § 6103) for calendar years 2025 through 2030 as modelled by the holidays library v{hv}: "
                 f"{len(rows)} rows giving each statutory date plus the observed (substitute) date when the holiday falls on a Saturday or Sunday. "
                 "Small enough to paste into a prompt; precise enough to drive payroll, SLA and deadline logic."),
        intended_uses=["Business-day arithmetic for US federal deadlines and banking cut-offs", "Payroll and scheduling calendars", "Quick validation of 'is this a federal holiday' questions"],
        not_intended_for=["State holidays (see the worldwide packet, subdivision rows for US)", "Stock exchange closures (see financial-market-holidays)", "Inauguration Day (DC-area only; not in the national calendar)"],
        coverage={"temporal": "2025-01-01 to 2030-12-31", "geographic": "United States, federal level", "row_count": len(rows)},
        source_ids=["holidays"],
        rights=rights_mit("MIT (Vacanza Team and contributors). Generated output of the holidays library; keep the notice with redistributed copies."),
        tables=[Table("us-federal-holidays", cols, rows, "One row per statutory or observed federal holiday date")],
        freshness={"as_of": SOURCES["holidays"]["published"][:10], "refresh_cadence": "Rebuild with each holidays release; federal holidays change rarely (last addition: Juneteenth, 2021)",
                   "known_staleness_risks": "A new federal holiday enacted by Congress or a one-off closure proclaimed by the President would not appear until the library is updated."},
        limitations=["Federal holidays only; state, local and market holidays are excluded.", "Observed dates follow the OPM rule (Saturday -> preceding Friday, Sunday -> following Monday) as implemented by the library.",
                     "Columbus Day is listed under its statutory name; Indigenous Peoples' Day observances are state-level.", "Inauguration Day (every fourth year, DC area only) is not a nationwide federal holiday and is not included."],
        transformations={"holidays": "imported the library and generated the US national calendar for 2025-2030 with and without observed-date rules"},
        agent_tasks=["Count business days between two dates for a federal filing deadline", "Shift a payment date that lands on 2027-12-24 (Christmas Day observed) to the next business day",
                     "Explain why 2026-07-03 is a day off for federal employees"],
        citation_subject=f"holidays {hv} (Vacanza Team, MIT), US national calendar 2025–2030",
        rights_plain=MIT_PLAIN,
        attribution_notice="Generated from the holidays library (https://github.com/vacanza/holidays), Copyright (c) Vacanza Team and individual contributors; Copyright (c) dr-prodigy 2017-2023; Copyright (c) ryanss 2014-2017. MIT License.",
        license_docs=[LicenseDoc(f"holidays {hv} — LICENSE (MIT)", license_text("holidays", 0)),
                      LicenseDoc(f"holidays {hv} — CONTRIBUTORS (referenced by the copyright notice)", license_text("holidays", 1))],
    )


def build_financial_markets() -> Packet:
    hmod = load_holidays_module()
    from holidays.registry import FINANCIAL
    rows, markets = [], []
    for mod, v in sorted(FINANCIAL.items(), key=lambda kv: kv[1][1]):
        clsname, mic = v[0], v[1]
        cls = getattr(hmod, clsname)
        cats = sorted(cls.supported_categories) or ["public"]
        langs = list(getattr(cls, "supported_languages", ()) or ())
        lang = "en_US" if "en_US" in langs else None
        recs = _holiday_records(hmod, hmod.financial_holidays, mic, None, cats, lang)
        mname = humanize_class_name(clsname)
        for (d, n) in sorted(recs, key=lambda k: (k[0], k[1])):
            r = recs[(d, n)]
            rows.append({"market_mic": mic, "market_name": mname, "date": d.isoformat(), "name": n, "categories": sorted(r["categories"]),
                         "observed": r["observed"], "estimated": "(estimated)" in n, "name_language": lang or ""})
        markets.append({"market_mic": mic, "market_name": mname, "categories": cats, "languages_supported": langs, "name_language": lang or "",
                        "start_year": getattr(cls, "start_year", None), "end_year": getattr(cls, "end_year", None), "rows_total": len(recs)})
    rows.sort(key=lambda r: (r["market_mic"], r["date"], r["name"]))
    cols = [Col("market_mic", "string", "ISO 10383 Market Identifier Code as used by the library (XNYS, XLON, XETR, XECB for the ECB TARGET calendar, ...)"),
            Col("market_name", "string", "Market name derived from the library's class name (e.g. NewYorkStockExchange -> New York Stock Exchange)"),
            Col("date", "date", "Calendar date, YYYY-MM-DD"), Col("name", "string", "Closure or event name"),
            Col("categories", "list", "Categories: public (full closure), half_day, restricted_settlement, ...; ';'-separated in CSV"),
            Col("observed", "boolean", "true for substitute dates generated because the holiday fell on a non-trading day"),
            Col("estimated", "boolean", "true when the library marks the date as an estimate"),
            Col("name_language", "string", "en_US when the English translation was requested; empty when names are English by construction")]
    mcols = [Col("market_mic", "string", "Market identifier"), Col("market_name", "string", "Derived market name"), Col("categories", "list", "Categories supported"),
             Col("languages_supported", "list", "Translations available"), Col("name_language", "string", "Language used"), Col("start_year", "integer", "First modelled year"),
             Col("end_year", "integer", "Last modelled year"), Col("rows_total", "integer", "Rows in market-holidays.csv")]
    hv = SOURCES["holidays"]["version"]
    return Packet(
        slug="financial-market-holidays-2025-2027",
        title="Financial market holidays 2025–2027 (exchanges and the ECB TARGET calendar)",
        kind="calendar",
        summary=(f"Trading holidays and half days for {len(markets)} financial markets (NYSE, NASDAQ, LSE, Xetra, Euronext-related ECB TARGET calendar, JPX, HKEX, ASX, TSX, "
                 f"B3, NSE/BSE and others) for 2025–2027, as modelled by the holidays library v{hv}: {len(rows)} rows keyed by market identifier code and date, "
                 "with categories separating full closures from half days and restricted-settlement days."),
        intended_uses=["Settlement-date and T+n arithmetic", "Market-open checks in trading and reporting agents", "Backtest calendars"],
        not_intended_for=["Intraday session times or early-close times", "Markets not in the list", "Authoritative exchange notices (always confirm with the venue for live trading)"],
        coverage={"temporal": "2025-01-01 to 2027-12-31", "geographic": f"{len(markets)} markets: " + ", ".join(m["market_mic"] for m in markets), "row_count": len(rows)},
        source_ids=["holidays"],
        rights=rights_mit("MIT (Vacanza Team and contributors). Generated output of the holidays library; keep the notice with redistributed copies."),
        tables=[Table("market-holidays", cols, rows, "Dated market closures and special sessions"),
                Table("markets", mcols, markets, "Markets covered, with categories and year ranges", role="documentation")],
        freshness={"as_of": SOURCES["holidays"]["published"][:10], "refresh_cadence": "Rebuild with each holidays release (roughly monthly)",
                   "known_staleness_risks": "Exchanges announce ad-hoc closures (national mourning, severe weather) and calendar changes with short notice; those are not reflected."},
        limitations=["Market names are derived from the library's class names and may differ from the venue's legal name (e.g. 'Ice Futures Europe').",
                     "Half-day closing times are not included.", "The library models the main equity session calendar; derivatives or bond segments may differ.",
                     "XECB is the European Central Bank TARGET2/T2 closing-day calendar, not an exchange."],
        transformations={"holidays": "imported the library and generated each market's calendar for 2025-2027 per category, with and without observed-date rules"},
        agent_tasks=["Determine the T+1 settlement date for a US equity trade executed on 2026-04-02", "List all days in 2026 on which both XLON and XNYS are closed",
                     "Check whether 2026-12-24 is a half day on NASDAQ"],
        citation_subject=f"holidays {hv} (Vacanza Team, MIT), financial market calendars 2025–2027",
        rights_plain=MIT_PLAIN,
        attribution_notice="Generated from the holidays library (https://github.com/vacanza/holidays), Copyright (c) Vacanza Team and individual contributors; Copyright (c) dr-prodigy 2017-2023; Copyright (c) ryanss 2014-2017. MIT License.",
        license_docs=[LicenseDoc(f"holidays {hv} — LICENSE (MIT)", license_text("holidays", 0)),
                      LicenseDoc(f"holidays {hv} — CONTRIBUTORS (referenced by the copyright notice)", license_text("holidays", 1))],
    )


# --------------------------------------------------------------------------- #
# pycountry (Debian iso-codes) packets
# --------------------------------------------------------------------------- #
LGPL_PLAIN = [
    "The data files are licensed under the GNU Lesser General Public License v2.1 only (LGPL-2.1-only), the licence Debian applies to the iso-codes project and pycountry applies to its copy.",
    "You may copy and redistribute the data, verbatim or modified, commercially or not, provided the LGPL-2.1 text and the copyright/attribution notices stay with it (RIGHTS.md carries both).",
    "This packet is a set of data files, not a program that links to a library: no code of yours becomes subject to the LGPL by reading these tables. If you redistribute a modified version of the tables themselves, that modified copy must remain under LGPL-2.1.",
    "There is no warranty. The LGPL does not mention model training; training is declared unspecified rather than permitted.",
    "ISO standards remain the intellectual property of ISO; the code lists here are the openly maintained Debian iso-codes reproduction of them, not the ISO publications.",
]


def rights_lgpl() -> dict:
    return {
        "license_family": "LGPL-2.1-only",
        "redistribution_allowed": "with-attribution",
        "training_allowed": "unspecified",
        "citation_required": False,
        "personal_data": "none",
        "notes": "LGPL-2.1-only applies to the data files (Debian iso-codes as vendored by pycountry 26.2.16). Redistribution is permitted with the licence text and the copyright notice intact; "
                 "modified copies of the data must stay under LGPL-2.1. The packet is the data files, not a derived program.",
    }


def _pycountry_db(name: str, top: str) -> list[dict]:
    root = fetch_source("pycountry")
    return json.loads((root / f"pycountry/databases/{name}.json").read_text(encoding="utf-8"))[top]


def _pycountry_packet(slug, title, kind, summary, table_stem, cols, rows, coverage_geo, intended, not_intended, limitations, tasks, citation_subject, extra_tables=None):
    pv = SOURCES["pycountry"]["version"]
    tables = [Table(table_stem, cols, rows, f"{title}: one row per code")] + (extra_tables or [])
    return Packet(
        slug=slug, title=title, kind=kind, summary=summary, intended_uses=intended, not_intended_for=not_intended,
        coverage={"temporal": f"Snapshot vendored in pycountry {pv} (released {SOURCES['pycountry']['published'][:10]})", "geographic": coverage_geo, "row_count": len(rows)},
        source_ids=["pycountry"], rights=rights_lgpl(), tables=tables,
        freshness={"as_of": SOURCES["pycountry"]["published"][:10],
                   "refresh_cadence": "pycountry releases a few times a year, tracking Debian iso-codes; rebuild on each release",
                   "known_staleness_risks": "ISO maintenance agencies publish newsletters with code additions, name changes and withdrawals; Debian iso-codes usually follows within weeks and pycountry within months. "
                                            "The artifact does not state which iso-codes release it vendors."},
        limitations=limitations, agent_tasks=tasks, citation_subject=citation_subject, rights_plain=LGPL_PLAIN,
        transformations={"pycountry": "read the pycountry/databases JSON table(s) verbatim and re-serialised them to CSV/JSON; only sort order and the derived columns named in the schema were added"},
        attribution_notice=f"Data from the Debian iso-codes project (https://salsa.debian.org/iso-codes-team/iso-codes) as distributed in pycountry {pv} (COPYRIGHT (c) 2008 - 2023, pycountry). Licensed under the GNU LGPL v2.1.",
        license_docs=[LicenseDoc(f"pycountry {pv} — COPYRIGHT.txt (copyright and attribution notice)", license_text("pycountry", 1)),
                      LicenseDoc("GNU Lesser General Public License, version 2.1 (LICENSE.txt as shipped in pycountry)", license_text("pycountry", 0))],
    )


def build_iso3166_1() -> Packet:
    rows = sorted(_pycountry_db("iso3166-1", "3166-1"), key=lambda r: r["alpha_2"])
    cols = [Col("alpha_2", "string", "ISO 3166-1 alpha-2 code"), Col("alpha_3", "string", "ISO 3166-1 alpha-3 code"), Col("numeric", "string", "ISO 3166-1 numeric code, zero-padded to 3 digits (kept as text)"),
            Col("name", "string", "Short name"), Col("official_name", "string", "Official (long) name where defined"), Col("common_name", "string", "Common name where it differs from the short name"),
            Col("flag", "string", "Regional-indicator emoji flag")]
    pv = SOURCES["pycountry"]["version"]
    return _pycountry_packet(
        "iso-3166-1-countries", "ISO 3166-1 country codes (alpha-2, alpha-3, numeric)", "code-list",
        f"The {len(rows)} officially assigned ISO 3166-1 country and territory codes with alpha-2, alpha-3 and numeric forms, short, official and common English names and the emoji flag, "
        f"taken verbatim from the Debian iso-codes tables vendored in pycountry {pv}. The canonical validation list for country fields in forms, APIs and datasets.",
        "iso-3166-1", cols, rows, f"{len(rows)} countries and territories (officially assigned codes only)",
        ["Validating country codes in forms, APIs and data pipelines", "Mapping between alpha-2, alpha-3 and numeric codes", "Display names for country selectors"],
        ["Political statements about sovereignty (the list reflects ISO assignments, not recognition)", "Historic or withdrawn codes (see iso-3166-3-historic-countries)", "Subdivisions (see iso-3166-2-subdivisions)"],
        ["Names are the ISO short names in English; they can differ from common usage (e.g. 'Korea, Republic of').", "User-assigned and exceptionally reserved codes (e.g. XK, UK, EU) are not included because ISO does not assign them.",
         "Translations exist upstream (pycountry locales) but are not included in this packet."],
        ["Validate the country field of a customer record and normalise 'USA' to 'US'", "Convert a list of alpha-3 codes from a trade dataset to alpha-2 for a mapping library",
         "Look up the official name to use in a contract for code 'NL'"],
        f"ISO 3166-1 code list, Debian iso-codes via pycountry {pv} (LGPL-2.1-only)")


def build_iso3166_2() -> Packet:
    rows = sorted(_pycountry_db("iso3166-2", "3166-2"), key=lambda r: r["code"])
    for r in rows:
        r["country_alpha_2"] = r["code"].split("-")[0]
    cols = [Col("code", "string", "ISO 3166-2 subdivision code (country alpha-2, hyphen, subdivision part)"), Col("country_alpha_2", "string", "ISO 3166-1 alpha-2 country prefix (derived from code)"),
            Col("name", "string", "Subdivision name (as in iso-codes; usually the local-language name transliterated)"), Col("type", "string", "Subdivision type (Province, State, Region, Parish, ...)"),
            Col("parent", "string", "ISO 3166-2 code of the parent subdivision where the country has more than one level")]
    pv = SOURCES["pycountry"]["version"]
    n_countries = len({r["country_alpha_2"] for r in rows})
    return _pycountry_packet(
        "iso-3166-2-subdivisions", "ISO 3166-2 subdivision codes (states, provinces, regions)", "code-list",
        f"All {len(rows):,} ISO 3166-2 principal subdivisions across {n_countries} countries, with code, name, subdivision type and parent subdivision, from the Debian iso-codes tables vendored in pycountry {pv}. "
        "Use it to validate state/province fields and to build hierarchical address or tax-jurisdiction lookups.",
        "iso-3166-2", cols, rows, f"{len(rows)} subdivisions in {n_countries} countries",
        ["Validating state/province/region codes", "Building region selectors and hierarchies", "Joining to holiday, tax or statistical datasets keyed by ISO 3166-2"],
        ["Postal or census geography (these are ISO subdivisions, not postal regions)", "Boundaries or coordinates", "Local-language names in native scripts"],
        ["Names are as published by iso-codes, generally romanised; no translations are included.", "Subdivision types are ISO's designations and vary in granularity between countries.",
         "Some countries' subdivisions change frequently; check the ISO 3166-2 newsletter for changes after the snapshot date."],
        ["Validate that 'US-CA' and 'CA-ON' are real subdivisions and return their names", "List every first-level subdivision of Italy with type 'Region'",
         "Map the holidays packet's subdivision suffixes (e.g. DE-BY) to official names"],
        f"ISO 3166-2 code list, Debian iso-codes via pycountry {pv} (LGPL-2.1-only)")


def build_iso3166_3() -> Packet:
    rows = sorted(_pycountry_db("iso3166-3", "3166-3"), key=lambda r: r["alpha_4"])
    cols = [Col("alpha_4", "string", "ISO 3166-3 four-letter code (former alpha-2 plus successor or 'HH'/'AA' marker)"), Col("alpha_2", "string", "Former ISO 3166-1 alpha-2 code"),
            Col("alpha_3", "string", "Former alpha-3 code"), Col("numeric", "string", "Former numeric code where one existed"), Col("name", "string", "Former country name"),
            Col("withdrawal_date", "string", "Year or date the code was withdrawn"), Col("comment", "string", "ISO comment on the change (successor codes, merger)")]
    pv = SOURCES["pycountry"]["version"]
    return _pycountry_packet(
        "iso-3166-3-historic-countries", "ISO 3166-3 formerly used country codes", "code-list",
        f"The {len(rows)} country codes withdrawn from ISO 3166-1 since 1974 (Yugoslavia, Zaire, the Soviet Union, Netherlands Antilles, ...), each with its ISO 3166-3 alpha-4 code, former alpha-2/alpha-3/numeric codes, "
        f"withdrawal date and ISO's comment on the successor codes, from Debian iso-codes via pycountry {pv}. Essential for cleaning historic datasets.",
        "iso-3166-3", cols, rows, f"{len(rows)} withdrawn codes",
        ["Interpreting country codes in historic datasets", "Explaining why a code no longer validates", "Mapping withdrawn codes to successors"],
        ["Current codes (see iso-3166-1-countries)", "Detailed succession rules (ISO's comment field is brief)"],
        ["Successor mappings are given only as free-text comments where ISO provides them.", "Withdrawal dates are sometimes only a year."],
        ["Explain what 'CS' meant in a 2004 dataset and which codes replaced it", "Flag rows in a legacy trade file whose country code is in this list"],
        f"ISO 3166-3 code list, Debian iso-codes via pycountry {pv} (LGPL-2.1-only)")


def build_iso4217() -> Packet:
    rows = sorted(_pycountry_db("iso4217", "4217"), key=lambda r: r["alpha_3"])
    cols = [Col("alpha_3", "string", "ISO 4217 alphabetic currency code"), Col("numeric", "string", "ISO 4217 numeric code, zero-padded (kept as text)"), Col("name", "string", "Currency name")]
    pv = SOURCES["pycountry"]["version"]
    return _pycountry_packet(
        "iso-4217-currencies", "ISO 4217 currency codes", "code-list",
        f"The {len(rows)} active ISO 4217 currency and fund codes (alphabetic code, numeric code, name) from Debian iso-codes via pycountry {pv}, including supranational units (XDR), precious metals (XAU, XAG) and test codes (XTS).",
        "iso-4217", cols, rows, f"{len(rows)} currency and fund codes",
        ["Validating currency fields", "Mapping numeric to alphabetic codes in payment messages", "Display names for currency selectors"],
        ["Exchange rates", "Minor-unit (decimal places) rules — not present in this source", "Historic currencies (ISO 4217 list three)"],
        ["Minor units (number of decimals) are not included; use ISO's own list or another packet for rounding rules.", "Country-to-currency mapping is not included (see the world-countries packet's currencies field).",
         "Withdrawn currencies (e.g. HRK after 2023) are absent once iso-codes removes them."],
        ["Validate the currency of an invoice line and print its name", "Convert numeric code 978 in an ISO 20022 message to 'EUR'"],
        f"ISO 4217 code list, Debian iso-codes via pycountry {pv} (LGPL-2.1-only)")


def build_iso639() -> Packet:
    rows = sorted(_pycountry_db("iso639-3", "639-3"), key=lambda r: r["alpha_3"])
    fam = sorted(_pycountry_db("iso639-5", "639-5"), key=lambda r: r["alpha_3"])
    cols = [Col("alpha_3", "string", "ISO 639-3 three-letter code"), Col("alpha_2", "string", "ISO 639-1 two-letter code where one exists"),
            Col("bibliographic", "string", "ISO 639-2/B bibliographic code where it differs from the terminology code"), Col("name", "string", "Reference name"),
            Col("inverted_name", "string", "Inverted name for sorting (e.g. 'Zhuang, Zuojiang')"), Col("common_name", "string", "Common name where different"),
            Col("scope", "string", "I = individual language, M = macrolanguage, S = special"), Col("type", "string", "L = living, H = historical, E = extinct, A = ancient, C = constructed, S = special")]
    fcols = [Col("alpha_3", "string", "ISO 639-5 language family or group code"), Col("name", "string", "Family/group name")]
    pv = SOURCES["pycountry"]["version"]
    return _pycountry_packet(
        "iso-639-languages", "ISO 639-3 language codes (with ISO 639-1/-2 mappings) and ISO 639-5 families", "code-list",
        f"All {len(rows):,} ISO 639-3 language identifiers with their ISO 639-1 two-letter and ISO 639-2/B bibliographic equivalents, reference and inverted names, scope and type, plus the {len(fam)} ISO 639-5 language family codes, "
        f"from Debian iso-codes via pycountry {pv}. The list to validate language tags against and to map between 2- and 3-letter codes.",
        "iso-639-3", cols, rows, f"{len(rows)} ISO 639-3 languages; {len(fam)} ISO 639-5 families",
        ["Validating language codes and BCP 47 primary subtags", "Mapping ISO 639-1 to ISO 639-3", "Language selectors and metadata normalisation"],
        ["Script or region subtags (see iso-15924-scripts and iso-3166-1-countries)", "Speaker counts or geography", "Autonyms (names in the language itself)"],
        ["Names are English reference names from ISO 639-3 (SIL); autonyms and translations are not included.", "Macrolanguage membership (which individual languages belong to 'zh') is not included.",
         "ISO 639-2 collective codes appear in the 639-5 table only where iso-codes lists them."],
        ["Normalise 'ger' (639-2/B) and 'de' (639-1) to 'deu' (639-3)", "Check whether 'yue' is an individual language or a macrolanguage before choosing a translation model",
         "Validate the primary language subtag of a BCP 47 tag such as 'pt-BR'"],
        f"ISO 639-3/639-5 code lists, Debian iso-codes via pycountry {pv} (LGPL-2.1-only)",
        extra_tables=[Table("iso-639-5-language-families", fcols, fam, "ISO 639-5 language families and groups")])


def build_iso15924() -> Packet:
    rows = sorted(_pycountry_db("iso15924", "15924"), key=lambda r: r["alpha_4"])
    cols = [Col("alpha_4", "string", "ISO 15924 four-letter script code"), Col("numeric", "string", "ISO 15924 numeric code (kept as text)"), Col("name", "string", "Script name")]
    pv = SOURCES["pycountry"]["version"]
    return _pycountry_packet(
        "iso-15924-scripts", "ISO 15924 script codes", "code-list",
        f"The {len(rows)} ISO 15924 writing-system codes (four-letter code, numeric code, English name), including special codes Zyyy (common), Zinh (inherited), Zxxx (unwritten) and Zzzz (uncoded), from Debian iso-codes via pycountry {pv}.",
        "iso-15924", cols, rows, f"{len(rows)} script codes",
        ["Validating BCP 47 script subtags", "Font and text-rendering pipelines", "Language-tag normalisation"],
        ["Unicode script property values (they overlap but are not identical)", "Character-to-script mapping"],
        ["Names are the ISO English names; French names and Unicode aliases upstream are not included.", "Scripts added by ISO after the snapshot are missing."],
        ["Validate the script subtag in 'sr-Latn-RS'", "Return the numeric code and name for 'Hans' when tagging simplified Chinese content"],
        f"ISO 15924 code list, Debian iso-codes via pycountry {pv} (LGPL-2.1-only)")


# --------------------------------------------------------------------------- #
# SPDX packets
# --------------------------------------------------------------------------- #
CC0_PLAIN = [
    "The packaged list is dedicated to the public domain under CC0 1.0 Universal: the packager waived all copyright and database rights, so you may copy, modify, redistribute and use it for any purpose, including commercially, with no attribution obligation.",
    "CC0 waives rights 'for any purpose whatsoever'; this packet therefore declares training use as permitted for the list itself. Nothing in CC0 grants trademark or patent rights.",
    "The SPDX License List is maintained by the SPDX Project (Linux Foundation). What this packet declares is the licence file shipped in the artifact (CC0-1.0); it does not independently verify the SPDX project's own terms.",
]

CC0_TEXT_PLAIN = CC0_PLAIN + [
    "The individual licence texts are the works of their respective authors and stewards (Free Software Foundation, Apache Software Foundation, Creative Commons, ...). Most permit verbatim copying but forbid altering the text. This packet reproduces them verbatim as SPDX publishes them; do not present a modified text as the licence.",
]


def _spdx_rows():
    root = fetch_source("spdx-license-list")
    ids_root = fetch_source("spdx-license-ids")
    meta = json.loads((root / "spdx.json").read_text(encoding="utf-8"))
    full = json.loads((root / "spdx-full.json").read_text(encoding="utf-8"))
    current = set(json.loads((ids_root / "index.json").read_text(encoding="utf-8")))
    deprecated = set(json.loads((ids_root / "deprecated.json").read_text(encoding="utf-8")))
    rows = []
    for lid in sorted(meta, key=lambda s: (s.lower(), s)):
        m = meta[lid]
        dep = True if lid in deprecated else (False if lid in current else None)
        rows.append({"id": lid, "name": m["name"], "url": m.get("url", ""), "osi_approved": bool(m.get("osiApproved")), "deprecated": dep,
                     "license_text": full[lid]["licenseText"], "text_sha256": sha256_bytes(full[lid]["licenseText"].encode("utf-8"))})
    newer = sorted(current - set(meta))
    return rows, newer


def _spdx_common(rows, newer):
    return dict(
        freshness={"as_of": "2026-02-20 (SPDX License List 3.28.0)",
                   "refresh_cadence": "SPDX publishes a new list roughly quarterly; spdx-license-list follows within weeks; rebuild on each release",
                   "known_staleness_risks": f"Identifiers added after 3.28.0 are missing. spdx-license-ids 3.0.24 (used for the deprecated flag) already lists {len(newer)} newer identifiers not in this packet: {', '.join(newer)}."},
        source_notes=["spdx-license-list 6.12.0 states in its readme: 'Using SPDX License List version 3.28.0 (2026-02-20)'. The deprecated flag comes from spdx-license-ids 3.0.24 (CC0-1.0, jslicense), "
                      "which is built from a later SPDX release; identifiers present in neither its current nor its deprecated list get an empty deprecated value.",
                      "The artifact's licenses/ directory also contains KiCad-libraries-exception.json, an exception rather than a licence; it is not in spdx.json and is not included."],
    )


def build_spdx_identifiers() -> Packet:
    rows, newer = _spdx_rows()
    id_rows = [{k: r[k] for k in ("id", "name", "url", "osi_approved", "deprecated")} for r in rows]
    cols = [Col("id", "string", "SPDX short identifier (case-sensitive; use exactly as written)"), Col("name", "string", "Full licence name"), Col("url", "string", "Reference URL for the licence as recorded by SPDX"),
            Col("osi_approved", "boolean", "true if the Open Source Initiative has approved the licence (per SPDX)"),
            Col("deprecated", "boolean", "true if SPDX has deprecated the identifier (e.g. GPL-2.0 -> GPL-2.0-only); empty when neither list of spdx-license-ids 3.0.24 mentions the identifier")]
    n_dep = sum(1 for r in id_rows if r["deprecated"] is True)
    n_unknown = sum(1 for r in id_rows if r["deprecated"] is None)
    common = _spdx_common(rows, newer)
    return Packet(
        slug="spdx-license-list-identifiers", title="SPDX License List 3.28.0 — identifiers and metadata", kind="code-list",
        summary=(f"All {len(id_rows)} licence identifiers of SPDX License List 3.28.0 (2026-02-20) with full name, reference URL, OSI-approval flag and a deprecated flag "
                 f"({n_dep} deprecated identifiers such as GPL-2.0 and LGPL-2.1; {n_unknown} legacy '+' identifiers left empty). The canonical vocabulary for licence fields in SBOMs, "
                 "package metadata and compliance tooling."),
        intended_uses=["Validating licence identifiers in SBOMs, package.json/pyproject metadata and policy engines", "Resolving a licence name to its canonical identifier", "Detecting deprecated identifiers before they reach a compliance document"],
        not_intended_for=["Licence exceptions (WITH clauses) — not included", "Compatibility analysis or legal interpretation", "Full licence texts (see spdx-license-texts)"],
        coverage={"temporal": "SPDX License List 3.28.0 (2026-02-20)", "geographic": "n/a (global vocabulary)", "row_count": len(id_rows)},
        source_ids=["spdx-license-list", "spdx-license-ids"],
        transformations={"spdx-license-list": "read spdx.json (id, name, url, osiApproved) and re-serialised it; sorted case-insensitively by id",
                         "spdx-license-ids": "read index.json and deprecated.json to set the deprecated flag for identifiers already in the list"},
        rights={"license_family": "CC0-1.0", "redistribution_allowed": True, "training_allowed": True, "citation_required": False, "personal_data": "none",
                "notes": "Both source packages declare CC0-1.0 (public-domain dedication); spdx-license-list ships the CC0 text, spdx-license-ids declares it in package.json only. No attribution is legally required; citing the SPDX list version is good practice."},
        tables=[Table("spdx-licenses", cols, id_rows, "One row per SPDX licence identifier, sorted case-insensitively by id")],
        limitations=["No 'fsfLibre' field: the source packages do not carry the FSF free/libre flag.", f"The deprecated flag is derived from a newer package (spdx-license-ids 3.0.24); {n_unknown} identifiers ending in '+' are in neither of its lists and are left empty.",
                     "Licence exceptions (e.g. Classpath-exception-2.0) are not part of this packet.", "The url field is the URL recorded by SPDX at the time and may be stale."],
        agent_tasks=["Validate every licence string in an SBOM and list the ones that are not SPDX identifiers", "Resolve 'GPL-2.0' to its non-deprecated replacement before writing a NOTICE file",
                     "Answer whether 'BUSL-1.1' is OSI approved"],
        citation_subject="SPDX License List 3.28.0 identifiers (spdx-license-list 6.12.0 + spdx-license-ids 3.0.24, CC0-1.0)",
        rights_plain=CC0_PLAIN,
        attribution_notice="SPDX License List 3.28.0, SPDX Project (Linux Foundation); repackaged as spdx-license-list 6.12.0 (Sindre Sorhus, CC0-1.0) and spdx-license-ids 3.0.24 (jslicense, CC0-1.0).",
        license_docs=[LicenseDoc("spdx-license-list 6.12.0 — license (CC0 1.0 Universal)", license_text("spdx-license-list", 0)),
                      LicenseDoc("spdx-license-ids 3.0.24 — declaration", 'The tarball carries no licence file. Its package.json declares:\n\n    "license": "CC0-1.0"\n')],
        **common,
    )


def build_spdx_texts() -> Packet:
    rows, newer = _spdx_rows()
    cols = [Col("id", "string", "SPDX short identifier"), Col("name", "string", "Full licence name"), Col("osi_approved", "boolean", "OSI approved per SPDX"),
            Col("deprecated", "boolean", "Deprecated identifier per spdx-license-ids 3.0.24; empty when unknown"), Col("url", "string", "Reference URL"),
            Col("license_text", "string", "Full licence text as published by SPDX (LF line endings; may contain template placeholders such as <year>)"),
            Col("text_sha256", "string", "SHA-256 of the UTF-8 licence text, for verifying a copy without re-downloading")]
    total_chars = sum(len(r["license_text"]) for r in rows)
    common = _spdx_common(rows, newer)
    return Packet(
        slug="spdx-license-texts", title="SPDX License List 3.28.0 — full licence texts", kind="license-text",
        summary=(f"The complete text of all {len(rows)} licences in SPDX License List 3.28.0 ({total_chars/1e6:.1f} million characters), keyed by SPDX identifier and accompanied by name, OSI flag, deprecated flag and a per-text SHA-256. "
                 "Lets an agent quote a licence exactly instead of paraphrasing it from memory, and verify that a LICENSE file in a repository is the canonical text."),
        intended_uses=["Quoting or shipping the exact text of a licence", "Comparing a repository's LICENSE file against the canonical text", "Generating NOTICE files"],
        not_intended_for=["Legal interpretation", "Licence exceptions (WITH clauses)", "Texts of licences added to SPDX after 3.28.0"],
        coverage={"temporal": "SPDX License List 3.28.0 (2026-02-20)", "geographic": "n/a", "row_count": len(rows)},
        source_ids=["spdx-license-list", "spdx-license-ids"],
        transformations={"spdx-license-list": "read spdx-full.json (metadata plus licenseText) verbatim; added a SHA-256 per text; sorted case-insensitively by id",
                         "spdx-license-ids": "read index.json and deprecated.json to set the deprecated flag"},
        rights={"license_family": "CC0-1.0", "redistribution_allowed": True, "training_allowed": "unspecified", "citation_required": False, "personal_data": "none",
                "notes": "CC0-1.0 covers the SPDX compilation and metadata as packaged. Each licence text remains the work of its own steward; most may be copied verbatim but not altered. Training is declared unspecified because the individual texts carry their own terms."},
        tables=[Table("spdx-license-texts", cols, rows, "One row per licence with its full text (the CSV has multi-line quoted text fields; the JSON twin is easier to parse)")],
        limitations=["Texts are SPDX's canonical/template versions; some contain placeholders (<year>, <copyright holders>) rather than a specific notice.",
                     "SPDX 'matching guidelines' allow small variations in real-world copies; a byte-for-byte comparison will reject legitimate variants.",
                     "Line endings are LF as shipped by the source package."],
        agent_tasks=["Resolve 'MIT' to its canonical SPDX text and paste it into a new repository", "Verify that a vendored LICENSE file matches Apache-2.0 by comparing SHA-256 after normalising whitespace",
                     "Produce a NOTICE bundle containing the exact texts of every licence in a dependency list"],
        citation_subject="SPDX License List 3.28.0 licence texts (spdx-license-list 6.12.0, CC0-1.0 compilation; texts © their respective stewards)",
        rights_plain=CC0_TEXT_PLAIN,
        attribution_notice="SPDX License List 3.28.0, SPDX Project (Linux Foundation); repackaged as spdx-license-list 6.12.0 (Sindre Sorhus, CC0-1.0). Individual licence texts are reproduced verbatim and remain the works of their respective authors.",
        license_docs=[LicenseDoc("spdx-license-list 6.12.0 — license (CC0 1.0 Universal)", license_text("spdx-license-list", 0)),
                      LicenseDoc("spdx-license-ids 3.0.24 — declaration", 'The tarball carries no licence file. Its package.json declares:\n\n    "license": "CC0-1.0"\n')],
        **common,
    )


# --------------------------------------------------------------------------- #
# IANA time zones (@vvo/tzdb + IANA tzdata via PyPI tzdata)
# --------------------------------------------------------------------------- #
TZ_SAMPLE_JAN = dt.datetime(2026, 1, 15, 12, tzinfo=dt.timezone.utc)
TZ_SAMPLE_JUL = dt.datetime(2026, 7, 15, 12, tzinfo=dt.timezone.utc)


def _tz_tables():
    tzroot = fetch_source("tzdata-pypi") / "tzdata" / "zoneinfo"
    zi = (tzroot / "tzdata.zi").read_text(encoding="utf-8").splitlines()
    version = zi[0].split()[-1] if zi and zi[0].startswith("# version") else "unknown"
    links, zones = {}, set()
    for ln in zi:
        p = ln.split()
        if not p:
            continue
        if p[0] == "L":
            links[p[2]] = p[1]
        elif p[0] == "Z":
            zones.add(p[1])
    zone_tab, zone1970 = {}, {}
    for ln in (tzroot / "zone.tab").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith("#"):
            p = ln.split("\t")
            zone_tab[p[2]] = p[0]
    for ln in (tzroot / "zone1970.tab").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith("#"):
            p = ln.split("\t")
            zone1970[p[2]] = p[0].split(",")
    country_names = {}
    for ln in (tzroot / "iso3166.tab").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith("#"):
            p = ln.split("\t")
            country_names[p[0]] = p[1]

    def load(name):
        with (tzroot.joinpath(*name.split("/"))).open("rb") as f:
            return zoneinfo.ZoneInfo.from_file(f, key=name)

    def facts(name):
        tz = load(name)
        canonical = links.get(name, name)
        j, u = TZ_SAMPLE_JAN.astimezone(tz), TZ_SAMPLE_JUL.astimezone(tz)
        offs = sorted({int(((TZ_SAMPLE_JAN.replace(day=1) + dt.timedelta(days=i)).astimezone(tz).utcoffset()).total_seconds() // 60) for i in range(365)})
        return {
            "iana_kind": "link" if name in links else ("zone" if name in zones else "unknown"),
            "iana_canonical": canonical,
            "iana_country_codes": zone1970.get(canonical, zone1970.get(name, [])),
            "iana_country_code": zone_tab.get(name, zone_tab.get(canonical, "")),
            "utc_offset_2026_01_15_minutes": int(j.utcoffset().total_seconds() // 60),
            "utc_offset_2026_07_15_minutes": int(u.utcoffset().total_seconds() // 60),
            "abbreviation_2026_01_15": j.tzname(),
            "abbreviation_2026_07_15": u.tzname(),
            "dst_active_2026_01_15": bool(j.dst()),
            "dst_active_2026_07_15": bool(u.dst()),
            "offsets_observed_2026_minutes": offs,
            "dst_in_2026": len(offs) > 1,
        }
    return version, links, zones, load, facts, country_names


def build_time_zones() -> Packet:
    vroot = fetch_source("vvo-tzdb")
    raw = json.loads((vroot / "raw-time-zones.json").read_text(encoding="utf-8"))
    names = json.loads((vroot / "time-zones-names.json").read_text(encoding="utf-8"))
    abbr = json.loads((vroot / "abbreviations.json").read_text(encoding="utf-8"))
    iana_version, links, zones, load, facts, country_names = _tz_tables()
    group_of = {}
    for z in raw:
        for member in z["group"]:
            group_of.setdefault(member, z["name"])
    rows = []
    for z in sorted(raw, key=lambda z: z["name"]):
        f = facts(z["name"])
        rows.append({"name": z["name"], "alternative_name": z["alternativeName"], "group": sorted(z["group"]), "continent_code": z["continentCode"], "continent_name": z["continentName"],
                     "country_code": z["countryCode"], "country_name": z["countryName"], "main_cities": z["mainCities"], "raw_offset_minutes": z["rawOffsetInMinutes"],
                     "abbreviation": z["abbreviation"], "raw_format": z["rawFormat"], **f})
    name_rows = []
    for n in sorted(names):
        f = facts(n)
        name_rows.append({"name": n, "iana_kind": f["iana_kind"], "iana_canonical": f["iana_canonical"], "in_time_zones_table": n in {z["name"] for z in raw},
                          "group_name": group_of.get(n, ""), "iana_country_code": f["iana_country_code"], "iana_country_codes": f["iana_country_codes"],
                          "utc_offset_2026_01_15_minutes": f["utc_offset_2026_01_15_minutes"], "utc_offset_2026_07_15_minutes": f["utc_offset_2026_07_15_minutes"],
                          "dst_in_2026": f["dst_in_2026"]})
    abbr_rows = [{"full_name": k, "abbreviation": v} for k, v in sorted(abbr.items())]
    raw_off_mismatch = [r["name"] for r in rows if r["raw_offset_minutes"] not in r["offsets_observed_2026_minutes"]]
    cols = [Col("name", "string", "IANA time zone identifier (the value to store; may be a backward-compatibility link in current tzdata, see iana_kind)"),
            Col("alternative_name", "string", "Human-friendly name from @vvo/tzdb (e.g. 'Pacific Time')"),
            Col("group", "list", "Zones @vvo/tzdb groups with this one (same country, same standard and DST offsets); ';'-separated in CSV"),
            Col("continent_code", "string", "Two-letter continent code (GeoNames)"), Col("continent_name", "string", "Continent name"),
            Col("country_code", "string", "ISO 3166-1 alpha-2 country code as assigned by @vvo/tzdb (GeoNames)"), Col("country_name", "string", "Country name as assigned by @vvo/tzdb"),
            Col("main_cities", "list", "Most populous cities in the zone group (GeoNames)"), Col("raw_offset_minutes", "integer", "Standard (non-DST) UTC offset in minutes as published by @vvo/tzdb (GeoNames-derived; see limitations)"),
            Col("abbreviation", "string", "Abbreviation as published by @vvo/tzdb"), Col("raw_format", "string", "Display string as published by @vvo/tzdb"),
            Col("iana_kind", "string", "zone = primary Zone in IANA " + iana_version + "; link = backward-compatibility alias"), Col("iana_canonical", "string", "Zone this name resolves to in IANA " + iana_version),
            Col("iana_country_codes", "list", "Countries the canonical zone serves per IANA zone1970.tab (first = most populous)"), Col("iana_country_code", "string", "Single country per IANA zone.tab"),
            Col("utc_offset_2026_01_15_minutes", "integer", "UTC offset in force at 2026-01-15T12:00Z, computed from IANA " + iana_version),
            Col("utc_offset_2026_07_15_minutes", "integer", "UTC offset in force at 2026-07-15T12:00Z, computed from IANA " + iana_version),
            Col("abbreviation_2026_01_15", "string", "Abbreviation in force at 2026-01-15 (IANA)"), Col("abbreviation_2026_07_15", "string", "Abbreviation in force at 2026-07-15 (IANA)"),
            Col("dst_active_2026_01_15", "boolean", "Whether daylight saving was in force at the January sample instant (IANA is_dst flag)"),
            Col("dst_active_2026_07_15", "boolean", "Whether daylight saving was in force at the July sample instant"),
            Col("offsets_observed_2026_minutes", "list", "Distinct UTC offsets observed at 12:00Z on each day of 2026"), Col("dst_in_2026", "boolean", "true when the zone changes offset during 2026")]
    ncols = [Col("name", "string", "IANA identifier (canonical zones and backward links as listed by @vvo/tzdb)"), Col("iana_kind", "string", "zone | link in IANA " + iana_version),
             Col("iana_canonical", "string", "Resolution target"), Col("in_time_zones_table", "boolean", "true when the name is a row of time-zones.csv"), Col("group_name", "string", "Row of time-zones.csv whose group contains this name"),
             Col("iana_country_code", "string", "Country per IANA zone.tab"), Col("iana_country_codes", "list", "Countries per IANA zone1970.tab"),
             Col("utc_offset_2026_01_15_minutes", "integer", "Offset at the January sample instant"), Col("utc_offset_2026_07_15_minutes", "integer", "Offset at the July sample instant"), Col("dst_in_2026", "boolean", "Offset changes during 2026")]
    acols = [Col("full_name", "string", "Long form of a time zone abbreviation as listed by @vvo/tzdb"), Col("abbreviation", "string", "Abbreviation (ambiguous: CST is used by several zones)")]
    return Packet(
        slug="iana-time-zones-current",
        title=f"IANA time zones with current offsets, DST, countries and cities (tzdata {iana_version})",
        kind="time-zones",
        summary=(f"{len(rows)} grouped IANA time zones from @vvo/tzdb 6.198.0 (alternative names, country, continent, main cities, standard offset) enriched at build time from IANA tzdata {iana_version} "
                 f"(via PyPI tzdata {SOURCES['tzdata-pypi']['version']}): whether each name is a canonical zone or a link, the countries it serves, the UTC offset and abbreviation in force on 2026-01-15 and 2026-07-15, "
                 f"IANA's own DST flags and every offset observed during 2026. Companion tables list all {len(name_rows)} identifiers (including backward-compatibility links) and {len(abbr_rows)} abbreviation expansions."),
        intended_uses=["Time zone pickers and validation of stored zone names", "Converting a local time to UTC when the exact offset on a date matters", "Explaining which cities and countries a zone covers"],
        not_intended_for=["Historical offsets before 2026 or transitions after 2026 (use tzdata directly)", "Sub-national legal time definitions", "Abbreviation-to-zone resolution (abbreviations are ambiguous)"],
        coverage={"temporal": f"Offsets sampled through calendar year 2026 from IANA {iana_version}; names and grouping as of @vvo/tzdb 6.198.0 (2025-12-01)", "geographic": "Worldwide", "row_count": len(rows),
                  "identifiers": len(name_rows), "abbreviations": len(abbr_rows)},
        source_ids=["vvo-tzdb", "tzdata-pypi"],
        transformations={"vvo-tzdb": "read raw-time-zones.json, time-zones-names.json and abbreviations.json verbatim and flattened them",
                         "tzdata-pypi": "loaded each zone with the stdlib zoneinfo module from the package's compiled files to compute 2026 offsets, abbreviations and DST flags; parsed tzdata.zi for links, zone.tab/zone1970.tab for countries"},
        rights={"license_family": "MIT", "redistribution_allowed": "with-attribution", "training_allowed": "unspecified", "citation_required": True, "personal_data": "none",
                "notes": "@vvo/tzdb is MIT (CodeAgain SASU); keep its notice with copies. Its country/city selections derive from GeoNames, which is CC BY 4.0 and requires attribution to GeoNames — hence citation_required. "
                         "Offsets, links and country tables are computed from the IANA Time Zone Database, which is in the public domain; the PyPI tzdata packaging is Apache-2.0 but no file from it is redistributed."},
        tables=[Table("time-zones", cols, rows, "Grouped zones with @vvo/tzdb descriptors and IANA-derived 2026 facts"),
                Table("time-zone-names", ncols, name_rows, "Every identifier @vvo/tzdb lists, with its IANA kind and 2026 offsets"),
                Table("time-zone-abbreviations", acols, abbr_rows, "Abbreviation expansions as published by @vvo/tzdb")],
        freshness={"as_of": f"IANA tzdata {iana_version}; @vvo/tzdb 6.198.0 (2025-12-01) built against IANA 2025b",
                   "refresh_cadence": "IANA releases several times a year, usually with weeks of notice before a government changes its rules; rebuild whenever PyPI tzdata or @vvo/tzdb updates",
                   "known_staleness_risks": "Governments change DST rules and standard offsets at short notice; a change announced after the IANA release used here is not reflected. "
                                            "@vvo/tzdb's raw_offset_minutes is GeoNames-derived and lags IANA in at least one case (" + ", ".join(raw_off_mismatch) + ")."},
        limitations=[
            "raw_offset_minutes, abbreviation and raw_format are copied from @vvo/tzdb (GeoNames-derived) and can disagree with the IANA-derived columns; " + (f"in this build they disagree for: {', '.join(raw_off_mismatch)}. Prefer the utc_offset_* columns." if raw_off_mismatch else "in this build they agree for every row."),
            "@vvo/tzdb groups zones by country and identical offsets, so time-zones.csv has one row per group (315) rather than one per IANA Zone (345); the group column lists the members.",
            "Etc/GMT±N zones, CST6CDT-style POSIX zones and the Factory zone are not listed by @vvo/tzdb and are therefore absent.",
            "dst_in_2026 is derived by sampling one instant per day; a zone that changes offset for less than a day would be missed (none is known).",
            "Abbreviations are not unique (CST, IST, BST each denote several zones); never resolve an abbreviation to a zone without a country.",
        ],
        agent_tasks=["Validate a user-supplied time zone name and normalise a link such as 'Asia/Calcutta' to its canonical 'Asia/Kolkata'", "Compute the UTC instant for '2026-07-15 09:00 America/Sao_Paulo' using the July offset column",
                     "Build a time zone selector grouped by continent with friendly names and main cities", "Decide whether to ask a user in 'Africa/Casablanca' about DST before scheduling a recurring meeting"],
        citation_subject=f"@vvo/tzdb 6.198.0 (MIT; GeoNames CC BY 4.0 derived) with IANA tzdata {iana_version} facts",
        rights_plain=MIT_PLAIN + [
            "Country and city selections originate from GeoNames (https://www.geonames.org/), licensed CC BY 4.0: when you publish data derived from those columns, credit GeoNames (e.g. 'Geographic data © GeoNames, CC BY 4.0').",
            "IANA Time Zone Database content is in the public domain ('This zic input file is in the public domain.' — tzdata.zi header); no obligation attaches to the offset, link and country columns.",
        ],
        attribution_notice="Time zone descriptors from @vvo/tzdb 6.198.0, Copyright (c) CodeAgain SASU, MIT License; city and country data © GeoNames (CC BY 4.0); offsets and links from the IANA Time Zone Database " + iana_version + " (public domain).",
        license_docs=[LicenseDoc("@vvo/tzdb 6.198.0 — LICENSE (MIT)", license_text("vvo-tzdb", 0)),
                      LicenseDoc(f"tzdata {SOURCES['tzdata-pypi']['version']} (PyPI) — LICENSE (Apache-2.0 packaging; IANA data public domain)", license_text("tzdata-pypi", 0))],
        source_notes=[f"IANA tzdata version read from the tzdata.zi header of PyPI tzdata {SOURCES['tzdata-pypi']['version']}: {iana_version}.",
                      "@vvo/tzdb 6.198.0 was generated against npm tzdata 1.0.46, whose timezone-data.json declares IANA version 2025b (checked on npm)."],
    )


# --------------------------------------------------------------------------- #
# US states and territories (python-us)
# --------------------------------------------------------------------------- #
BSD3_PLAIN = [
    "You may use and redistribute the data, with or without modification, provided the copyright notice, the list of conditions and the disclaimer are kept with source copies and reproduced in the documentation of binary copies.",
    "You may not use the name Sunlight Labs or its contributors to endorse or promote products derived from the data without written permission.",
    "No warranty. The licence does not mention model training; training is declared unspecified.",
]


def build_us_states() -> Packet:
    root = fetch_source("us")
    src = (root / "us" / "states.py").read_text(encoding="utf-8")
    recs = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "State":
            d = ast.literal_eval([k for k in node.value.keywords if k.arg is None][0].value)
            recs.append(d)
    rows = []
    for d in sorted(recs, key=lambda r: r["abbr"]):
        status = "obsolete" if d["is_obsolete"] else ("territory" if d["is_territory"] else ("federal_district" if d["abbr"] == "DC" else "state"))
        rows.append({"abbr": d["abbr"], "name": d["name"], "fips": d["fips"], "status": status, "is_territory": d["is_territory"], "is_obsolete": d["is_obsolete"],
                     "is_contiguous": d["is_contiguous"], "is_continental": d["is_continental"], "statehood_year": d["statehood_year"], "capital": d["capital"],
                     "capital_tz": d["capital_tz"], "ap_abbr": d["ap_abbr"], "time_zones": d["time_zones"]})
    cols = [Col("abbr", "string", "USPS two-letter abbreviation"), Col("name", "string", "Name"), Col("fips", "string", "Two-digit FIPS 5-2 state code (numerically identical to the ANSI INCITS 38 state code); empty for obsolete entries"),
            Col("status", "string", "state | federal_district | territory | obsolete (derived from the source flags; DC is the federal district)"), Col("is_territory", "boolean", "Territory flag from the source"),
            Col("is_obsolete", "boolean", "Historic entity (Dakota Territory, Orleans Territory, Philippine Islands)"), Col("is_contiguous", "boolean", "Part of the contiguous 48 states (plus DC)"),
            Col("is_continental", "boolean", "On the North American continent (contiguous states plus Alaska)"), Col("statehood_year", "integer", "Year of admission to the Union; empty for non-states"),
            Col("capital", "string", "Capital city; empty for DC and some obsolete entries"), Col("capital_tz", "string", "IANA time zone of the capital"), Col("ap_abbr", "string", "AP Stylebook abbreviation where one exists"),
            Col("time_zones", "list", "IANA time zones used in the state as listed by the source (includes some legacy link names); ';'-separated in CSV")]
    n_states = sum(1 for r in rows if r["status"] == "state")
    return Packet(
        slug="us-states-and-territories", title="US states, federal district and territories (names, USPS, FIPS, capitals, time zones)", kind="code-list",
        summary=(f"{len(rows)} rows covering the {n_states} US states, the District of Columbia, five inhabited territories (AS, GU, MP, PR, VI) and three obsolete historic entities, from python-us 3.2.0: "
                 "USPS abbreviation, name, two-digit FIPS code, capital and its time zone, all IANA time zones in use, AP style abbreviation, statehood year and contiguous/continental flags."),
        intended_uses=["Validating and normalising US state fields", "Joining FIPS-coded Census data to state names", "Choosing a time zone for state-level scheduling"],
        not_intended_for=["County or place FIPS codes", "GNIS/ANSI feature identifiers (not in the source)", "Population, area or boundary data"],
        coverage={"temporal": "python-us 3.2.0 (released 2024-07-22); the underlying facts change very rarely", "geographic": "United States: 50 states, DC, 5 territories, 3 obsolete entities", "row_count": len(rows)},
        source_ids=["us"],
        transformations={"us": "parsed us/states.py with the ast module (no code executed) and flattened each State record; added the derived status column; dropped the name_metaphone helper field"},
        rights={"license_family": "BSD-3-Clause", "redistribution_allowed": "with-attribution", "training_allowed": "unspecified", "citation_required": False, "personal_data": "none",
                "notes": "BSD-3-Clause (Copyright (c) 2014, Sunlight Labs). Keep the notice, conditions and disclaimer with redistributed copies; do not use the Sunlight Labs name for endorsement."},
        tables=[Table("us-states", cols, rows, "One row per state, district, territory or obsolete entity, sorted by abbreviation")],
        freshness={"as_of": SOURCES["us"]["published"][:10], "refresh_cadence": "Rebuild when python-us releases; content is essentially static", "known_staleness_risks": "A new state or territory status change would require a source update; none is pending."},
        limitations=["The source carries no ANSI/GNIS identifier column; the FIPS code is numerically identical to the ANSI INCITS 38 numeric state code but the eight-digit GNIS feature ID is not included.",
                     "The time_zones list includes legacy IANA link names (e.g. America/Indianapolis) exactly as the source lists them; resolve them with the iana-time-zones-current packet.",
                     "Minor outlying islands and freely associated states (FM, MH, PW) are not included by the source.", "DC is listed with status federal_district; python-us itself excludes it from STATES unless DC_STATEHOOD is set."],
        agent_tasks=["Normalise 'Calif.', 'CA' and 'California' to one abbreviation and FIPS code 06", "Attach state names to a Census CSV keyed by two-digit FIPS", "Pick the capital's time zone when scheduling a call with a state agency in Arizona"],
        citation_subject="python-us 3.2.0 (BSD-3-Clause, Sunlight Labs / unitedstates project)",
        rights_plain=BSD3_PLAIN,
        attribution_notice="Data from python-us 3.2.0 (https://github.com/unitedstates/python-us), Copyright (c) 2014, Sunlight Labs. BSD 3-Clause License.",
        license_docs=[LicenseDoc("us 3.2.0 — LICENSE (BSD-3-Clause)", license_text("us", 0))],
    )


# --------------------------------------------------------------------------- #
# World countries (mledoze/countries, ODbL)
# --------------------------------------------------------------------------- #
ODBL_PLAIN = [
    "The database is licensed under the Open Data Commons Open Database License v1.0 (ODbL-1.0). You may copy, share, adapt and use it, including commercially.",
    "Attribution: any public use of the database or of a work produced from it must carry a notice naming the source (mledoze/countries) and the licence, e.g. 'Contains information from mledoze/countries, which is made available under the Open Database License (ODbL)'.",
    "Share-alike: if you publicly distribute an adapted (derivative) database, you must offer it under ODbL-1.0 as well, and on request make it available in machine-readable form.",
    "Keep open: if you distribute the database with technical measures that restrict use (DRM), you must also make an unrestricted copy available.",
    "Produced Works (charts, applications, model outputs) may be under any licence but must carry the attribution notice. Whether a trained model is a Produced Work or a Derivative Database is not settled by the licence text, so training is declared unspecified.",
]


def build_world_countries() -> Packet:
    root = fetch_source("world-countries")
    raw_bytes = (root / "countries.json").read_bytes()
    data = json.loads(raw_bytes.decode("utf-8"))
    rows = []
    for c in sorted(data, key=lambda c: c["cca2"]):
        rows.append({
            "cca2": c["cca2"], "cca3": c["cca3"], "ccn3": c["ccn3"], "cioc": c["cioc"],
            "name_common": c["name"]["common"], "name_official": c["name"]["official"],
            "native_names_official": {k: v["official"] for k, v in c["name"].get("native", {}).items()},
            "native_names_common": {k: v["common"] for k, v in c["name"].get("native", {}).items()},
            "independent": c["independent"], "status": c["status"], "un_member": c["unMember"], "un_regional_group": c.get("unRegionalGroup", ""),
            "region": c["region"], "subregion": c["subregion"], "capital": c["capital"],
            "currencies": sorted(c["currencies"]), "currency_names": {k: v["name"] for k, v in sorted(c["currencies"].items())}, "currency_symbols": {k: v.get("symbol", "") for k, v in sorted(c["currencies"].items())},
            "idd_root": c["idd"].get("root", ""), "idd_suffixes": c["idd"].get("suffixes", []), "tld": c["tld"],
            "languages": dict(sorted(c["languages"].items())), "latitude": c["latlng"][0] if c["latlng"] else None, "longitude": c["latlng"][1] if len(c["latlng"]) > 1 else None,
            "landlocked": c["landlocked"], "borders": c["borders"], "area_km2": c["area"], "flag": c["flag"],
            "demonym_eng_m": c.get("demonyms", {}).get("eng", {}).get("m", ""), "demonym_eng_f": c.get("demonyms", {}).get("eng", {}).get("f", ""),
            "alt_spellings": c["altSpellings"],
        })
    cols = [Col("cca2", "string", "ISO 3166-1 alpha-2"), Col("cca3", "string", "ISO 3166-1 alpha-3"), Col("ccn3", "string", "ISO 3166-1 numeric (text)"), Col("cioc", "string", "International Olympic Committee code"),
            Col("name_common", "string", "Common English name"), Col("name_official", "string", "Official English name"),
            Col("native_names_official", "map", "Official name per native language (ISO 639-3 key); 'key=value;...' in CSV"), Col("native_names_common", "map", "Common name per native language"),
            Col("independent", "boolean", "Sovereign state per the source; empty where the source has null (Kosovo)"), Col("status", "string", "ISO 3166-1 assignment status (officially-assigned | user-assigned)"), Col("un_member", "boolean", "UN member state"),
            Col("un_regional_group", "string", "UN regional group; empty where none"), Col("region", "string", "Region (UN M49 style)"), Col("subregion", "string", "Subregion"),
            Col("capital", "list", "Capital city or cities"), Col("currencies", "list", "ISO 4217 codes in use"), Col("currency_names", "map", "Currency names by code"), Col("currency_symbols", "map", "Currency symbols by code"),
            Col("idd_root", "string", "International dialling root (e.g. +4)"), Col("idd_suffixes", "list", "Dialling suffixes; root + suffix = calling code (the US/Canada list enumerates area codes)"),
            Col("tld", "list", "Country-code top-level domains"), Col("languages", "map", "Official languages: ISO 639-3 code -> English name"),
            Col("latitude", "number", "Representative latitude"), Col("longitude", "number", "Representative longitude"), Col("landlocked", "boolean", "Landlocked"),
            Col("borders", "list", "Land borders as alpha-3 codes"), Col("area_km2", "number", "Land area in square kilometres"), Col("flag", "string", "Emoji flag as in the source (empty for BQ)"),
            Col("demonym_eng_m", "string", "English demonym (male form)"), Col("demonym_eng_f", "string", "English demonym (female form)"), Col("alt_spellings", "list", "Alternative spellings and names")]
    n_indep = sum(1 for r in rows if r["independent"])
    return Packet(
        slug="world-countries", title="World countries: names, codes, capitals, currencies, languages, borders (ODbL)", kind="country-profiles",
        summary=(f"{len(rows)} countries and territories from mledoze/countries (world-countries 5.1.0) flattened into one row each: ISO 3166-1 codes, IOC code, common/official/native names, sovereignty and UN membership, region, capitals, "
                 f"currencies with names and symbols, dialling codes, TLDs, official languages, coordinates, land borders, area, emoji flag and English demonyms ({n_indep} independent states). "
                 "The upstream JSON with 20+ name translations per country is included verbatim as an alternate file. Licensed ODbL-1.0: attribution and share-alike apply."),
        intended_uses=["Enriching country codes with names, capitals, currencies and neighbours", "Building country pickers with flags and dialling codes", "Feature engineering (region, landlocked, area, borders)"],
        not_intended_for=["Authoritative ISO code validation (use iso-3166-1-countries, which is the ISO list)", "Legal boundaries or disputed-territory positions", "Current exchange rates or population figures"],
        coverage={"temporal": "world-countries 5.1.0 (published 2025-02-26)", "geographic": f"{len(rows)} countries and territories (ISO 3166-1 plus Kosovo as user-assigned XK)", "row_count": len(rows)},
        source_ids=["world-countries"],
        transformations={"world-countries": "flattened countries.json into one row per country (nested names, currencies, languages and idd split into columns; translations and demonyms other than English left to the verbatim upstream file)"},
        rights={"license_family": "ODbL-1.0", "redistribution_allowed": "share-alike", "training_allowed": "unspecified", "citation_required": True, "personal_data": "none",
                "notes": "Open Database License 1.0. Public use requires attribution to mledoze/countries and the ODbL notice; a redistributed adapted database must itself be ODbL-1.0; DRM-restricted copies need an open parallel copy. "
                         "Produced works may carry any licence but must keep the notice."},
        tables=[Table("world-countries", cols, rows, "Flattened country records sorted by cca2 (map-valued fields are 'key=value;...' in CSV and objects in JSON)")],
        extra_files=[ExtraFile("data/countries.upstream.json", "alternate", "json", "The upstream countries.json exactly as shipped in world-countries 5.1.0 (nested names, translations, demonyms in many languages)", raw_bytes, rows=len(data))],
        freshness={"as_of": SOURCES["world-countries"]["published"][:10], "refresh_cadence": "Upstream releases irregularly (roughly yearly); rebuild on each npm release",
                   "known_staleness_risks": "Currency changes, capital relocations and dialling-plan changes after February 2025 are not reflected; the data is community-curated and not an official register."},
        limitations=["Community-curated: names, capitals and language lists are editorial choices, not an official register; independence and UN membership flags follow the maintainer's criteria.",
                     "Kosovo (XK) is a user-assigned code, not an ISO-assigned one.", "Coordinates are a single representative point, not centroids of legal boundaries.",
                     "GeoJSON/TopoJSON outlines and SVG flags exist upstream (18 MB) but are not included here.", "The CSV encodes maps as 'key=value;...' strings; use the JSON form when you need structure."],
        agent_tasks=["Return the capital, currency code and dialling code for 'DE' in one lookup", "List all landlocked countries in Africa with their neighbours", "Populate a phone-number field with the right country calling code from an ISO code",
                     "Get the French and Japanese translations of a country's name from the upstream JSON"],
        citation_subject="mledoze/countries (world-countries 5.1.0), ODbL-1.0",
        rights_plain=ODBL_PLAIN,
        attribution_notice="Contains information from mledoze/countries (https://github.com/mledoze/countries, world-countries 5.1.0), which is made available here under the Open Database License (ODbL) 1.0.",
        license_docs=[LicenseDoc("world-countries 5.1.0 — LICENSE (Open Database License v1.0)", license_text("world-countries", 0))],
        source_notes=["The npm registry 'license' field for this package is empty. The tarball's package.json carries a 'licenses' array naming ODbL-1.0 and the tarball ships the full ODbL text as LICENSE; that file is what this packet relies on."],
    )


# --------------------------------------------------------------------------- #
# NAICS 2017 (npm naics; US Census Bureau workbook)
# --------------------------------------------------------------------------- #
def _read_xlsx_rows(xlsx: Path) -> list[dict]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(xlsx) as z:
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])) for si in sst.findall("m:si", ns)]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.find("m:sheetData", ns):
        vals = {}
        for c in row:
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            t = c.get("t")
            v = c.find("m:v", ns)
            if t == "s":
                vals[col] = strings[int(v.text)]
            elif t == "inlineStr":
                vals[col] = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
            else:
                vals[col] = v.text if v is not None else ""
        rows.append(vals)
    return rows


NAICS_LEVELS = {2: "sector", 3: "subsector", 4: "industry_group", 5: "naics_industry", 6: "national_industry"}


def build_naics_2017() -> Packet:
    root = fetch_source("naics")
    xlsx = root / "data" / "2017_NAICS_Descriptions.xlsx"
    sheet = _read_xlsx_rows(xlsx)
    header = [sheet[0].get(c, "") for c in ("A", "B", "C")]
    if header != ["Code", "Title", "Description"]:
        raise SystemExit(f"unexpected NAICS header: {header}")
    recs = []
    for r in sheet[1:]:
        code = r.get("A", "").strip()
        title = r.get("B", "").strip()
        tri = bool(title) and title.endswith("T") and (title[-2].islower() or title[-2] == ")")
        if tri:
            title = title[:-1].rstrip()
        desc = r.get("C", "").replace("\r\n", "\n").strip()
        if desc == "NULL":
            desc = ""
        recs.append({"code": code, "title": title, "trilateral_comparable": tri, "description": desc})
    codes = {r["code"] for r in recs}
    ranges = [c for c in codes if "-" in c]

    def sector_of(prefix2: str) -> str:
        if prefix2 in codes:
            return prefix2
        for rg in ranges:
            lo, hi = rg.split("-")
            if lo <= prefix2 <= hi:
                return rg
        return ""

    for r in recs:
        c = r["code"]
        n = 2 if "-" in c else len(c)
        r["level_number"] = n
        r["level"] = NAICS_LEVELS[n]
        r["sector_code"] = c if n == 2 else sector_of(c[:2])
        r["parent_code"] = "" if n == 2 else (sector_of(c[:2]) if n == 3 else c[:-1])
        if n > 3 and r["parent_code"] not in codes:
            raise SystemExit(f"NAICS parent missing for {c}")
    recs.sort(key=lambda r: r["code"])
    cols = [Col("code", "string", "NAICS 2017 code (2 to 6 digits; sectors 31-33, 44-45 and 48-49 are ranges)"), Col("level_number", "integer", "Hierarchy depth: 2 sector, 3 subsector, 4 industry group, 5 NAICS industry, 6 national industry"),
            Col("level", "string", "sector | subsector | industry_group | naics_industry | national_industry"), Col("title", "string", "Title with the Census trilateral 'T' marker removed"),
            Col("trilateral_comparable", "boolean", "true when the Census workbook marks the title with superscript T: a level at which Canada, Mexico and the United States agreed to maintain comparable definitions"),
            Col("sector_code", "string", "Owning sector code (derived)"), Col("parent_code", "string", "Immediate parent code (derived; empty for sectors)"),
            Col("description", "string", "Full description text from the Census workbook, including illustrative examples and cross-references (LF line breaks); empty where the workbook holds NULL (most 4-digit industry groups)")]
    n_by_level = {lvl: sum(1 for r in recs if r["level"] == lvl) for lvl in NAICS_LEVELS.values()}
    return Packet(
        slug="naics-2017-codes", title="NAICS 2017 codes, titles and descriptions (US Census Bureau workbook)", kind="classification",
        summary=(f"All {len(recs):,} codes of the 2017 North American Industry Classification System, United States edition — {n_by_level['sector']} sectors, {n_by_level['subsector']} subsectors, {n_by_level['industry_group']} industry groups, "
                 f"{n_by_level['naics_industry']} NAICS industries and {n_by_level['national_industry']} national industries — with titles, full descriptions, a derived parent/sector hierarchy and the Census trilateral-comparability marker, "
                 "parsed directly from the Census Bureau workbook 2017_NAICS_Descriptions.xlsx that ships inside the npm package naics 1.2.1. Note: the 2017 edition was superseded by NAICS 2022; use this packet for 2017-vintage data."),
        intended_uses=["Decoding NAICS codes in 2017-vintage datasets (2017 Economic Census, older SBA/PPP files, historic filings)", "Building the sector -> national industry hierarchy for roll-ups", "Concordance work between the 2017 and 2022 editions (this packet supplies the 2017 side)"],
        not_intended_for=["Classifying new establishments (use NAICS 2022, the current edition)", "Canadian or Mexican national industries below the 5-digit level", "Size standards or tax rules attached to codes"],
        coverage={"temporal": "NAICS 2017 (US edition; workbook created 2017-04-27, modified 2017-05-03 per its document properties); superseded by NAICS 2022", "geographic": "United States", "row_count": len(recs)},
        source_ids=["naics"],
        transformations={"naics": "parsed data/2017_NAICS_Descriptions.xlsx (sheet, shared strings) with the stdlib; stripped the trailing superscript-T marker into a boolean; derived level, sector and parent codes; normalised NULL descriptions to empty"},
        rights={"license_family": "MIT", "redistribution_allowed": "with-attribution", "training_allowed": "unspecified", "citation_required": False, "personal_data": "none",
                "notes": "The npm package is MIT (Copyright (c) 2020 ntdalbec); keep its notice with copies. The classification itself is published by the US Census Bureau for the Office of Management and Budget; as a work of the US federal government it is not subject to US copyright, "
                         "but this packet declares only the licence carried by the artifact it was built from."},
        tables=[Table("naics-2017", cols, recs, "One row per NAICS 2017 code, sorted by code string")],
        freshness={"as_of": "2017-05-03 (workbook modification date); packaged 2020-04-22", "refresh_cadence": "None expected: NAICS 2017 is a closed edition. NAICS 2022 is current and a 2027 revision is in preparation; a separate packet would be needed for those.",
                   "known_staleness_risks": "Edition staleness is the main risk: codes created, split or merged in NAICS 2022 (e.g. in retail trade and information) are not reflected."},
        limitations=["2017 edition only; NAICS 2022 (effective 2022) changed roughly 5% of six-digit codes, and NAICS 2027 is being prepared.",
                     "The workbook is the Census 'Descriptions' file; index entries (illustrative example lists) and the 2017-2012 concordance are not included.",
                     "The trilateral flag is recovered from a typographic marker (a trailing 'T' that Census renders as superscript); the rule applied is documented in the code and matched 807 titles with no ambiguous cases.",
                     "Some 5-digit descriptions read 'See industry description for NNNNNN.' because the six-digit US industry is identical, exactly as in the workbook.",
                     f"{sum(1 for r in recs if not r['description'])} industry-group (4-digit) rows have no description in the workbook (the cell holds the literal text NULL); they are emitted as empty strings.",
                     "The tarball never states 'US Census Bureau' in words; the origin is established by file identity (file name, sheet name and internal folder path '...\\naics\\2017NAICS\\' match the Census publication at census.gov/naics/2017NAICS/2017_NAICS_Descriptions.xlsx)."],
        agent_tasks=["Expand NAICS code 541511 in a 2019 loan dataset to its title and sector", "Roll a list of six-digit 2017 codes up to subsector for a summary table",
                     "Check whether a five-digit code is trilaterally comparable before merging US data with Canadian data"],
        citation_subject="NAICS 2017 (US Census Bureau, 2017_NAICS_Descriptions.xlsx) via npm naics 1.2.1 (MIT)",
        rights_plain=MIT_PLAIN + ["The NAICS classification is a work of the US federal government (Census Bureau / OMB); US Government works are not subject to copyright in the United States. This packet nevertheless declares only the MIT licence found in the artifact and does not claim public-domain status on its own authority."],
        attribution_notice="NAICS 2017 descriptions from the US Census Bureau workbook 2017_NAICS_Descriptions.xlsx as included in npm package naics 1.2.1, Copyright (c) 2020 ntdalbec, MIT License.",
        license_docs=[LicenseDoc("naics 1.2.1 — LICENSE (MIT)", license_text("naics", 0))],
        source_notes=["Origin evidence inside the artifact: data/2017_NAICS_Descriptions.xlsx (sheet '2017_NAICS_Descriptions', internal path 'M:\\Classification\\GOODSERV\\ECDB - Sadowski Websites\\naics\\2017NAICS\\', created 2017-04-27); "
                      "the README documents the classes as 'defined in the 2017 NAICS Descriptions document'. The Census Bureau publishes a file of that name at https://www.census.gov/naics/2017NAICS/2017_NAICS_Descriptions.xlsx."],
    )


# --------------------------------------------------------------------------- #
# Rendering: README.md, RIGHTS.md, manifest.json, checksums, zip
# --------------------------------------------------------------------------- #
def md_escape_cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def source_record(sid: str, transformation: str = DEFAULT_TRANSFORMATION) -> dict:
    s = SOURCES[sid]
    return {
        "name": s["package"] + " " + s["version"],
        "url": s.get("homepage") or s["url"],
        "version_or_edition": s["version"],
        "transformation": transformation,
        "kind": s["kind"],
        "package": s["package"],
        "version": s["version"],
        "artifact_filename": s["filename"],
        "artifact_url": s["url"],
        "artifact_sha256": s["sha256"],
        "published": s.get("published"),
        "upstream": s["upstream"],
        "homepage": s.get("homepage"),
        "license": s["license"],
        "license_files": s.get("license_files", []),
        "fetched_at": s.get("fetched_at"),
        "role": s.get("role", "data"),
        "notes": s.get("notes", ""),
    }


def render_readme(p: Packet, files_meta: list[dict], primary_sha: str) -> str:
    L = []
    L.append(f"# {p.title}\n")
    L.append(f"*Graunt seed packet `{p.slug}` v{VERSION} · kind: {p.kind} · family: {FAMILY} · built {BUILT_AT}*\n")
    L.append(p.summary + "\n")
    L.append("## What you get\n")
    L.append("| File | Role | Format | Rows | Description |\n|---|---|---|---:|---|")
    for f in files_meta:
        rows = f"{f['rows']:,}" if f.get("rows") is not None else ""
        L.append(f"| `{f['path']}` | {f['role']} | {f['format']} | {rows} | {md_escape_cell(f['description'])} |")
    L.append("| `manifest.json` | manifest | json | | Machine-readable description: schema, hashes, rights, provenance, freshness |")
    L.append("| `RIGHTS.md` | license | markdown | | Declared rights, plain-language obligations, verbatim licence texts |")
    L.append("| `checksums.sha256` | integrity | text | | SHA-256 of every file in the packet |\n")
    L.append("Sizes and SHA-256 per file are in `manifest.json#structure.files` and `checksums.sha256`.\n")
    L.append("CSV files are UTF-8 with a header row, RFC 4180 quoting (fields containing commas, quotes or line breaks are double-quoted, embedded quotes doubled), LF line endings, "
             "booleans as `true`/`false`, empty string for null, lists `;`-separated and maps as `key=value;key=value`. JSON files are arrays of objects with the same records and field order, "
             "one record per line, with native booleans, numbers, arrays and objects.\n")
    L.append("## What it helps an agent do\n")
    for t in p.agent_tasks:
        L.append(f"- {t}")
    L.append("")
    L.append("## Coverage\n")
    for k, v in p.coverage.items():
        L.append(f"- **{k}**: {v:,}" if isinstance(v, int) else f"- **{k}**: {v}")
    L.append("")
    L.append("### Intended uses\n")
    for u in p.intended_uses:
        L.append(f"- {u}")
    L.append("\n### Not intended for\n")
    for u in p.not_intended_for:
        L.append(f"- {u}")
    L.append("")
    L.append("## Schema\n")
    for t in p.tables:
        L.append(f"### `data/{t.stem}.csv`" + (f" / `data/{t.stem}.json`" if t.json_twin else "") + "\n")
        L.append("| column | type | description |\n|---|---|---|")
        for c in t.columns:
            L.append(f"| `{c.name}` | {c.type} | {md_escape_cell(c.description)} |")
        L.append("")
    L.append("## Known limitations\n")
    for l in p.limitations:
        L.append(f"- {l}")
    L.append("")
    L.append("## Freshness\n")
    L.append(f"- **Data as of**: {p.freshness['as_of']}")
    L.append(f"- **Cadence**: {p.freshness['refresh_cadence']}")
    L.append(f"- **Staleness risks**: {p.freshness['known_staleness_risks']}\n")
    L.append("## Provenance\n")
    L.append("Built by `packets/build.py` in the graunt-plugin repository from pinned, hash-verified upstream artifacts. No file was fetched from anywhere except the PyPI and npm registries.\n")
    L.append("| source | kind | version | artifact sha256 | licence | published | fetched |\n|---|---|---|---|---|---|---|")
    for sid in p.source_ids:
        s = SOURCES[sid]
        L.append(f"| {s['package']} | {s['kind']} | {s['version']} | `{s['sha256']}` | {s['license']} | {s.get('published','')[:10]} | {s.get('fetched_at','')} |")
    L.append("")
    for sid in p.source_ids:
        s = SOURCES[sid]
        L.append(f"- **{s['package']} {s['version']}** — upstream authority: {s['upstream']}. {s.get('notes','')}")
    for n in p.source_notes:
        L.append(f"- {n}")
    L.append("")
    for heading, body in p.readme_extra:
        L.append(f"## {heading}\n\n{body}\n")
    L.append("## Rights\n")
    r = p.rights
    L.append(f"- **licence family**: {r['license_family']}\n- **redistribution**: {csv_cell(r['redistribution_allowed'])}\n- **training**: {csv_cell(r['training_allowed'])}\n- **citation required**: {csv_cell(r['citation_required'])}\n- **personal data**: {r['personal_data']}\n")
    L.append(r["notes"] + " See `RIGHTS.md` for the obligations in plain words and the verbatim licence text. Declarations are the publisher's statements, not legal advice.\n")
    L.append("## Citation\n")
    L.append("```\n" + citation(p, primary_sha) + "\n```\n")
    L.append(f"Attribution notice to carry with derived work: {p.attribution_notice}\n")
    return "\n".join(L)


def citation(p: Packet, primary_sha: str) -> str:
    return f"{p.citation_subject}, retrieved from Graunt packet {p.slug} v{VERSION}, sha256 {primary_sha}"


def render_rights(p: Packet) -> str:
    r = p.rights
    yn = {True: "yes", False: "no"}
    redist = r["redistribution_allowed"]
    train = r["training_allowed"]
    L = [f"# Rights declaration — {p.title}\n",
         f"Packet `{p.slug}` v{VERSION}. These are the seller's declarations, derived from the licence files found inside the source artifacts "
         "(paths and SHA-256 pinned in `packets/sources.json` and repeated in `manifest.json#sources[].license_files`). They are not legal advice; "
         "a missing or ambiguous declaration is to be read as restrictive.\n",
         "| field | value |\n|---|---|",
         f"| license_family | {r['license_family']} |", f"| redistribution_allowed | {csv_cell(redist)} |", f"| training_allowed | {csv_cell(train)} |",
         f"| citation_required | {csv_cell(r['citation_required'])} |", f"| personal_data | {r['personal_data']} |", "",
         "## Who holds the rights\n", p.rights_holder, "",
         "## License family granted to buyers\n", f"**{r['license_family']}** — the same licence the source artifact carries; nothing more is granted. {r['notes']}", "",
         "## What buyers may do\n",
         "- Read and use internally: yes",
         f"- Redistribute the content to their own users: {yn.get(redist, redist)}",
         f"- Use it to train or fine-tune models: {yn.get(train, 'not addressed by the licence (declared unspecified)')}",
         f"- Citation required: {yn[r['citation_required']]}" + (" — use the attribution line below" if r["citation_required"] else " — the attribution line below is still the right way to credit the source"),
         "", "## What the licence requires of you\n"]
    for b in p.rights_plain:
        L.append(f"- {b}")
    L += ["", "## Sources and their licenses\n", "| Source | URL | License | What was taken | Obligations that propagate |\n|---|---|---|---|---|"]
    for sid in p.source_ids:
        src = SOURCES[sid]
        taken = p.transformations.get(sid, DEFAULT_TRANSFORMATION)
        L.append(f"| {src['package']} {src['version']} ({src['kind']}) | {src.get('homepage') or src['url']} | {src['license']} | {md_escape_cell(taken)} | {PROPAGATING_OBLIGATIONS.get(src['license'], 'see licence text')} |")
    L += ["", "## Personal data\n"] + [f"- {n}" for n in p.safety_notes] + [f"- Declared value: `{r['personal_data']}`.", "",
          "## Attribution line\n", f"> {p.attribution_notice}", "",
          "## License texts\n", "Reproduced verbatim from the downloaded artifacts.\n"]
    for doc in p.license_docs:
        L.append(f"### {doc.heading}\n")
        L.append("```text\n" + doc.text.rstrip("\n") + "\n```\n")
    return "\n".join(L)


def zip_packet(pdir: Path, zpath: Path) -> None:
    ts = NOW.timetuple()[:6]
    files = sorted(p for p in pdir.rglob("*") if p.is_file())
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            zi = zipfile.ZipInfo(f"{pdir.name}/{f.relative_to(pdir).as_posix()}", date_time=ts)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, f.read_bytes())


def write_packet(p: Packet) -> dict:
    pdir = OUT / p.slug
    if pdir.exists():
        shutil.rmtree(pdir)
    (pdir / "data").mkdir(parents=True)
    files_meta, schema = [], []
    for t in p.tables:
        cols = [c.name for c in t.columns]
        n = write_csv(pdir / "data" / f"{t.stem}.csv", cols, t.rows)
        files_meta.append({"path": f"data/{t.stem}.csv", "role": t.role, "format": "csv", "rows": n, "description": t.description})
        schema.append({"file": f"data/{t.stem}.csv", "columns": [{"name": c.name, "type": c.type, "description": c.description} for c in t.columns]})
        if t.json_twin:
            n2 = write_json(pdir / "data" / f"{t.stem}.json", cols, t.rows)
            files_meta.append({"path": f"data/{t.stem}.json", "role": "alternate" if t.role == "primary" else t.role, "format": "json", "rows": n2,
                               "description": f"JSON form of data/{t.stem}.csv (same records and field order; lists and maps as arrays and objects)"})
            schema.append({"file": f"data/{t.stem}.json", "same_as": f"data/{t.stem}.csv"})
    for ef in p.extra_files:
        (pdir / ef.path).parent.mkdir(parents=True, exist_ok=True)
        (pdir / ef.path).write_bytes(ef.content)
        files_meta.append({"path": ef.path, "role": ef.role, "format": ef.format, "rows": ef.rows, "description": ef.description})
    primary = next(f for f in files_meta if f["role"] == "primary")
    primary_sha = sha256_file(pdir / primary["path"])
    (pdir / "RIGHTS.md").write_text(render_rights(p), encoding="utf-8")
    (pdir / "README.md").write_text(render_readme(p, files_meta, primary_sha), encoding="utf-8")
    files_meta.append({"path": "README.md", "role": "documentation", "format": "md", "rows": None, "description": "Listing text: what it is, agent tasks, coverage, schema, limitations, cadence, provenance, citation"})
    files_meta.append({"path": "RIGHTS.md", "role": "license", "format": "md", "rows": None, "description": "Declared rights, obligations in plain words, verbatim licence texts"})
    for f in files_meta:
        fp = pdir / f["path"]
        f["bytes"] = fp.stat().st_size
        f["sha256"] = sha256_file(fp)
    manifest = {
        "graunt_staging_manifest": MANIFEST_SCHEMA,
        "slug": p.slug, "title": p.title, "version": VERSION, "built_at": BUILT_AT,
        "family": FAMILY, "kind": p.kind,
        "summary": p.summary,
        "intended_uses": p.intended_uses, "not_intended_for": p.not_intended_for,
        "coverage": p.coverage,
        "sources": [source_record(sid, p.transformations.get(sid, DEFAULT_TRANSFORMATION)) for sid in p.source_ids],
        "rights": p.rights,
        "structure": {"files": files_meta, "schema": schema, "primary_file": primary["path"], "csv_dialect": {"encoding": "utf-8", "header": True, "quoting": "RFC 4180 minimal", "line_terminator": "LF", "list_separator": ";", "map_format": "key=value;key=value", "booleans": "true/false", "null": ""}},
        "freshness": p.freshness,
        "evidence_refs": {
            "INTEGRITY": ["checksums.sha256", "manifest.json#structure.files[].sha256"],
            "STRUCTURE": ["manifest.json#structure", "README.md#schema"],
            "FRESHNESS": ["manifest.json#freshness", "README.md#freshness"],
            "RIGHTS": ["RIGHTS.md", "manifest.json#rights", "manifest.json#sources[].license_files"],
            "SAFETY": ["RIGHTS.md#personal-data", "manifest.json#rights.personal_data"],
            "ORIGIN": ["manifest.json#sources", "README.md#provenance", "packets/sources.json (pinned artifact and licence-file hashes)"],
            "DELIVERY_MATCH": ["README.md#what-you-get", "manifest.json#structure.files"],
        },
        "limitations": p.limitations,
        "safety_notes": p.safety_notes,
        "citation_template": citation(p, primary_sha),
        "build": {"tool": "graunt-plugin/packets/build.py", "python": sys.version.split()[0], "sources_file_sha256": sha256_file(SOURCES_FILE)},
    }
    (pdir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    all_files = sorted(f for f in pdir.rglob("*") if f.is_file())
    lines = [f"{sha256_file(f)}  {f.relative_to(pdir).as_posix()}" for f in all_files]
    (pdir / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    validate_packet(pdir, manifest)
    zpath = OUT / f"{p.slug}.zip"
    zip_packet(pdir, zpath)
    zsha = sha256_file(zpath)
    (OUT / f"{p.slug}.zip.sha256").write_text(f"{zsha}  {zpath.name}\n", encoding="utf-8")
    with zipfile.ZipFile(zpath) as z:
        names = set(z.namelist())
    expected = {f"{p.slug}/{f.relative_to(pdir).as_posix()}" for f in pdir.rglob("*") if f.is_file()}
    if names != expected:
        raise SystemExit(f"zip content mismatch for {p.slug}: {names ^ expected}")
    entry = {"slug": p.slug, "title": p.title, "kind": p.kind, "version": VERSION, "rows": primary["rows"], "primary_file": primary["path"],
             "license_family": p.rights["license_family"], "redistribution_allowed": p.rights["redistribution_allowed"], "training_allowed": p.rights["training_allowed"],
             "citation_required": p.rights["citation_required"], "sources": [f"{SOURCES[s]['package']}@{SOURCES[s]['version']}" for s in p.source_ids],
             "as_of": p.freshness["as_of"], "dir": f"packets/{p.slug}/", "manifest_sha256": sha256_file(pdir / "manifest.json"),
             "zip": {"path": f"packets/{p.slug}.zip", "bytes": zpath.stat().st_size, "sha256": zsha},
             "uncompressed_bytes": sum(f.stat().st_size for f in pdir.rglob("*") if f.is_file())}
    log(f"  built {p.slug}: {primary['rows']:,} rows, zip {human_bytes(entry['zip']['bytes'])}, sha256 {zsha[:16]}…")
    return entry


def validate_packet(pdir: Path, manifest: dict) -> None:
    for f in manifest["structure"]["files"]:
        fp = pdir / f["path"]
        if not fp.exists():
            raise SystemExit(f"missing file {fp}")
        if fp.stat().st_size != f["bytes"] or sha256_file(fp) != f["sha256"]:
            raise SystemExit(f"hash/size mismatch for {fp}")
        if f["format"] == "csv":
            n = read_csv_rows(fp)
            if n != f["rows"]:
                raise SystemExit(f"row count mismatch {fp}: manifest {f['rows']} != file {n}")
            fp.read_text(encoding="utf-8")  # must be valid UTF-8
        elif f["format"] == "json" and f.get("rows") is not None:
            n = read_json_rows(fp)
            if n != f["rows"]:
                raise SystemExit(f"row count mismatch {fp}: manifest {f['rows']} != file {n}")
    for line in (pdir / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        if sha256_file(pdir / rel) != digest:
            raise SystemExit(f"checksum mismatch {rel}")
    if manifest["coverage"]["row_count"] != next(f["rows"] for f in manifest["structure"]["files"] if f["path"] == manifest["structure"]["primary_file"]):
        raise SystemExit(f"coverage.row_count does not match primary file for {manifest['slug']}")


# --------------------------------------------------------------------------- #
# Index and rejections
# --------------------------------------------------------------------------- #
def write_index(entries: list[dict]) -> None:
    total_zip = sum(e["zip"]["bytes"] for e in entries)
    total_unc = sum(e["uncompressed_bytes"] for e in entries)
    L = ["# Graunt seed packets — index\n",
         f"Version **{VERSION}**, built {BUILT_AT} by `packets/build.py`. {len(entries)} packets, {human_bytes(total_zip)} zipped ({human_bytes(total_unc)} unpacked). "
         "Every packet directory contains `data/` (CSV + JSON), `manifest.json`, `README.md`, `RIGHTS.md` and `checksums.sha256`; `<slug>.zip.sha256` holds the archive hash.\n",
         "| slug | title | kind | rows | licence | redistribution | training | zip size | zip sha256 |\n|---|---|---|---:|---|---|---|---:|---|"]
    for e in entries:
        L.append(f"| `{e['slug']}` | {md_escape_cell(e['title'])} | {e['kind']} | {e['rows']:,} | {e['license_family']} | {csv_cell(e['redistribution_allowed'])} | {csv_cell(e['training_allowed'])} | {human_bytes(e['zip']['bytes'])} | `{e['zip']['sha256']}` |")
    L.append("")
    L.append("## Sources pinned for this build\n")
    L.append("| source | kind | version | role | licence | artifact sha256 | published |\n|---|---|---|---|---|---|---|")
    for sid, s in SOURCES.items():
        L.append(f"| {s['package']} | {s['kind']} | {s['version']} | {s.get('role','data')} | {s['license']} | `{s['sha256']}` | {s.get('published','')[:10]} |")
    L.append("\nRejected candidates and rights judgment calls are documented in `REJECTED.md`.\n")
    (DIST / "INDEX.md").write_text("\n".join(L), encoding="utf-8")
    index = {"graunt_staging_index": MANIFEST_SCHEMA, "version": VERSION, "built_at": BUILT_AT, "packet_count": len(entries), "zip_bytes_total": total_zip,
             "uncompressed_bytes_total": total_unc, "packets": entries,
             "sources": {sid: {k: s.get(k) for k in ("kind", "package", "version", "role", "license", "sha256", "url", "published", "fetched_at", "upstream")} for sid, s in SOURCES.items()}}
    (DIST / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


REJECTED_MD = """# Rejected candidates and rights judgment calls

Graunt sells rights honesty, so this file records every candidate source that was looked at and turned down, and every accepted source
that needed a judgment call. The test applied throughout: **the licence must be established from the LICENSE file or metadata inside the
downloaded artifact itself**, the upstream authority must be identifiable, and the packet must never declare more rights than that text supports.

## Rejected

### airport-codes (npm) {version} — REJECTED: rights and provenance unclear

- Artifact: `{filename}`, sha256 `{sha256}` ({published_note}).
- Declared licence: {declared}.
- Why rejected: {reason}
- What would change the decision: a release that ships the OpenFlights ODbL/DbCL notice, states the source and snapshot date of every data file, and drops or attributes the unexplained `airports.csv`. Even then the packet would have to be declared ODbL-1.0 with share-alike, not ISC.

## Considered and not used as a source

- **holidays financial-market data beyond MIC-coded venues, holidays translations, pycountry `locales/`, world-countries GeoJSON/TopoJSON/SVG files**: rights are fine (same licences as the accepted artifacts) but they were out of scope for this seed batch; nothing about rights blocked them.
- **Official SPDX `licenses.json` (spdx.org / GitHub), Census `2017_NAICS_Descriptions.xlsx` (census.gov), IANA `tzdata` tarball (iana.org), GeoNames dumps**: these are the true upstreams for several packets but the build environment can only reach PyPI and npm. Each packet records the upstream authority in `manifest.json#sources[].upstream` and declares only the licence of the artifact actually used.

## Accepted only after a specific rights check (judgment calls)

1. **world-countries 5.1.0 (npm)** — the npm registry `license` field is empty, which would normally be disqualifying. The tarball's `package.json` carries a `licenses` array naming ODbL-1.0 and the tarball ships the full ODbL 1.0 text as `LICENSE` (sha256 pinned). Accepted as **ODbL-1.0** with attribution and share-alike declared; training declared unspecified.
2. **naics 1.2.1 (npm)** — the tarball never says "US Census Bureau" in words. The edition is explicit (2017, in the README, the file name and the sheet name). The Census origin was established by file identity: the workbook is named `2017_NAICS_Descriptions.xlsx`, its sheet is `2017_NAICS_Descriptions`, its embedded save path ends in `naics\\2017NAICS\\`, and the Census Bureau publishes a workbook of exactly that name at census.gov/naics/2017NAICS/. Accepted as **MIT** (the artifact's licence) with a note that the classification is a US Government work; the packet does not claim public-domain status on its own authority. The 2017 edition is superseded by NAICS 2022, which the README states prominently.
3. **@vvo/tzdb 6.198.0 (npm)** — MIT LICENSE present, but the README says the country and city data are generated from GeoNames, which is CC BY 4.0. Accepted as **MIT with citation_required = true** so that GeoNames attribution is carried; the IANA-derived columns (public domain) were computed independently from PyPI tzdata 2026.4 (IANA 2026d) rather than trusted from the package, and the package's own GeoNames-derived standard offset is flagged where it disagrees with IANA (Africa/Windhoek).
4. **spdx-license-ids 3.0.24 (npm)** — no LICENSE file in the tarball; CC0-1.0 is declared in `package.json` only. Used solely to supply the boolean `deprecated` flag for identifiers already present in spdx-license-list 6.12.0 (which does ship the CC0 text). Recorded as a second source with its own hash; the six legacy `+` identifiers it does not mention are left empty rather than guessed.
5. **spdx-license-texts** — CC0-1.0 covers the packaged compilation, not the individual licence texts, which remain the works of their stewards and generally forbid modification. Declared **CC0-1.0** for the compilation with training set to unspecified and a plain-words warning not to alter texts; the identifiers packet (metadata only) declares training permitted because CC0 waives rights "for any purpose whatsoever".
6. **pycountry 26.2.16 (PyPI)** — LGPL-2.1-only applies to the data files themselves (Debian iso-codes). Declared as such, with the statement that redistribution is permitted with the licence and notice intact and that the packet is data files, not a derived program. The artifact does not state which iso-codes release it vendors; freshness is therefore given as the pycountry release date.
7. **holidays 0.104 (PyPI)** — MIT; the copyright notice refers to a CONTRIBUTORS file of ~220 personal names. That file is reproduced in RIGHTS.md to keep the notice intact; the data files themselves carry no personal data.
8. **Training rights in general** — MIT, BSD-3-Clause, LGPL-2.1 and ODbL are silent on model training, so every packet under them declares `training_allowed: "unspecified"` rather than inferring permission from a general "without restriction" grant. Only CC0 (identifiers packet) is declared `true`, on the strength of its "any purpose whatsoever" waiver.
"""


def write_rejected() -> None:
    r = REJECTED["airport-codes"]
    (DIST / "REJECTED.md").write_text(REJECTED_MD.format(version=r["version"], filename=r["filename"], sha256=r["sha256"], published_note=r["published_note"],
                                                         declared=r["declared_license"], reason=r["reason"]), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
BUILDERS = [
    ("public-holidays-2025-2027-worldwide", build_holidays_worldwide),
    ("us-federal-holidays-2025-2030", build_us_federal),
    ("financial-market-holidays-2025-2027", build_financial_markets),
    ("iso-3166-1-countries", build_iso3166_1),
    ("iso-3166-2-subdivisions", build_iso3166_2),
    ("iso-3166-3-historic-countries", build_iso3166_3),
    ("iso-4217-currencies", build_iso4217),
    ("iso-639-languages", build_iso639),
    ("iso-15924-scripts", build_iso15924),
    ("spdx-license-list-identifiers", build_spdx_identifiers),
    ("spdx-license-texts", build_spdx_texts),
    ("iana-time-zones-current", build_time_zones),
    ("us-states-and-territories", build_us_states),
    ("world-countries", build_world_countries),
    ("naics-2017-codes", build_naics_2017),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated slugs to build (others are left untouched; index is rebuilt from the manifests present)")
    ap.add_argument("--clean", action="store_true", help="delete dist/ (including the download cache) before building")
    ap.add_argument("--list", action="store_true", help="list packet slugs and exit")
    args = ap.parse_args(argv)
    if args.list:
        for slug, _ in BUILDERS:
            print(slug)
        return 0
    if args.clean and DIST.exists():
        shutil.rmtree(DIST)
    only = set(args.only.split(",")) if args.only else None
    if only is None and OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    log(f"Graunt seed packets v{VERSION} — built_at {BUILT_AT}; cache {CACHE}")
    log("Fetching pinned sources…")
    for sid in SOURCES:
        fetch_source(sid)
    entries = []
    for slug, fn in BUILDERS:
        if only and slug not in only:
            continue
        log(f"Building {slug}…")
        entries.append(write_packet(fn()))
    if only:
        # merge with existing manifests so the index stays complete
        built = {e["slug"] for e in entries}
        for slug, _ in BUILDERS:
            if slug in built:
                continue
            m = OUT / slug / "manifest.json"
            if m.exists():
                man = json.loads(m.read_text(encoding="utf-8"))
                zpath = OUT / f"{slug}.zip"
                prim = next(f for f in man["structure"]["files"] if f["path"] == man["structure"]["primary_file"])
                entries.append({"slug": slug, "title": man["title"], "kind": man["kind"], "version": man["version"], "rows": prim["rows"], "primary_file": prim["path"],
                                "license_family": man["rights"]["license_family"], "redistribution_allowed": man["rights"]["redistribution_allowed"], "training_allowed": man["rights"]["training_allowed"],
                                "citation_required": man["rights"]["citation_required"], "sources": [f"{s['package']}@{s['version']}" for s in man["sources"]], "as_of": man["freshness"]["as_of"],
                                "dir": f"packets/{slug}/", "manifest_sha256": sha256_file(m), "zip": {"path": f"packets/{slug}.zip", "bytes": zpath.stat().st_size, "sha256": sha256_file(zpath)},
                                "uncompressed_bytes": sum(f.stat().st_size for f in (OUT / slug).rglob("*") if f.is_file())})
        order = {slug: i for i, (slug, _) in enumerate(BUILDERS)}
        entries.sort(key=lambda e: order[e["slug"]])
    write_index(entries)
    write_rejected()
    total = sum(e["zip"]["bytes"] for e in entries)
    log(f"Done: {len(entries)} packets, {human_bytes(total)} zipped -> {DIST}/INDEX.md, index.json, REJECTED.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
