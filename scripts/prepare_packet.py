#!/usr/bin/env python3
"""
prepare_packet.py — turn files into a Graunt packet staging bundle.

Stdlib only. Three subcommands:

  init      <path> --out DIR [--title T] [--slug S]
            Copy the input file(s) into DIR/data/, inventory them (size, sha256,
            format, role), infer CSV/JSON schemas and row counts, flag columns
            that look like personal data, and write:
              DIR/manifest.json      structure + evidence refs filled, rights marked REQUIRED
              DIR/RIGHTS.md          rights declaration skeleton
              DIR/README.md          listing text skeleton (what / tasks / coverage / limits)
              DIR/checksums.sha256   sha256sum-format checksums
  validate  DIR
            Refuse to pass while any REQUIRED placeholder remains, any hash or
            row count disagrees with the files, or a rights field holds a value
            Graunt cannot interpret. Exit 0 only when the bundle is ready to upload.
  checksums DIR
            Re-generate checksums.sha256 after you edit README.md or RIGHTS.md.
  zip       DIR [--out FILE]
            Deterministic zip of the bundle (sorted entries, fixed timestamps).

The bundle is what you upload at https://graunt.com/prepare. Graunt drafts the
listing from the files; you confirm rights and price before anything is public.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

REQUIRED = "REQUIRED"
# A placeholder is the skeleton form only: "REQUIRED: ..." or a bare REQUIRED table cell / line.
# Plain prose such as "UNLESS REQUIRED BY APPLICABLE LAW" (GPL/LGPL texts pasted into RIGHTS.md) must not trip it.
PLACEHOLDER_RE = re.compile(r"\bREQUIRED\b[ \t]*(:|\|)|(^|\|)[ \t]*REQUIRED[ \t]*$", re.M)
MANIFEST_VERSION = "0.1"
SAMPLE_ROWS = 5000
DATA_EXT = {
    ".csv": "csv", ".tsv": "tsv", ".json": "json", ".jsonl": "jsonl", ".ndjson": "jsonl",
    ".parquet": "parquet", ".xlsx": "xlsx", ".xls": "xls", ".xml": "xml", ".sqlite": "sqlite",
    ".db": "sqlite", ".geojson": "geojson", ".yaml": "yaml", ".yml": "yaml", ".txt": "text",
    ".md": "markdown", ".pdf": "pdf", ".html": "html", ".zip": "zip",
}
DOC_FORMATS = {"markdown", "text", "pdf", "html"}
PERSONAL_DATA_HINTS = re.compile(
    r"(^|_|\b)(email|e_mail|phone|mobile|tel|ssn|social_security|dob|date_of_birth|birth|"
    r"first_name|last_name|full_name|surname|given_name|address|street|zip_code|postcode|"
    r"ip_address|ip|passport|license_number|licence_number|npi|dea|patient|customer_id|user_id|"
    r"lat|latitude|lon|longitude|geo)(_|\b|$)",
    re.I,
)
ALLOWED_TRI = {True, False, "unspecified"}
ALLOWED_REDIST = {True, False, "with-attribution", "share-alike", "unspecified"}
ALLOWED_PERSONAL = {"none", "contains-personal-data", "pseudonymised", "aggregated-only"}


# ---------- helpers ----------

def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return re.sub(r"-{2,}", "-", s)[:80] or "packet"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def guess_type(values: list[str]) -> str:
    vals = [v for v in values if v not in ("", None)]
    if not vals:
        return "string"
    def all_match(fn):
        return all(fn(v) for v in vals)
    if all_match(lambda v: v.lower() in ("true", "false", "0", "1", "yes", "no", "y", "n", "t", "f")):
        if any(v.lower() in ("true", "false", "yes", "no", "y", "n", "t", "f") for v in vals):
            return "boolean"
    if all_match(lambda v: re.fullmatch(r"[-+]?\d{1,18}", v) is not None):
        return "integer"
    if all_match(lambda v: re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", v) is not None):
        return "number"
    if all_match(lambda v: re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) is not None):
        return "date"
    if all_match(lambda v: re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[-+]\d{2}:?\d{2})?", v) is not None):
        return "datetime"
    return "string"


def profile_csv(p: Path, fmt: str) -> dict:
    delimiter = "\t" if fmt == "tsv" else ","
    with p.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
        head = f.read(65536)
        f.seek(0)
        if fmt == "csv":
            try:
                delimiter = csv.Sniffer().sniff(head, delimiters=",;|\t").delimiter
            except csv.Error:
                delimiter = ","
        reader = csv.reader(f, delimiter=delimiter)
        try:
            header = next(reader)
        except StopIteration:
            return {"rows": 0, "columns": [], "delimiter": delimiter, "warnings": ["empty file"]}
        samples: list[list[str]] = [[] for _ in header]
        empties = [0] * len(header)
        rows = 0
        ragged = 0
        for row in reader:
            rows += 1
            if len(row) != len(header):
                ragged += 1
            if rows <= SAMPLE_ROWS:
                for i in range(min(len(row), len(header))):
                    v = row[i]
                    samples[i].append(v)
                    if v == "":
                        empties[i] += 1
    sampled = min(rows, SAMPLE_ROWS)
    columns = []
    for i, name in enumerate(header):
        col = {"name": name, "type": guess_type(samples[i]), "description": REQUIRED + ": what this column means, units if numeric"}
        if sampled:
            col["empty_share_in_sample"] = round(empties[i] / sampled, 4)
        if PERSONAL_DATA_HINTS.search(name or ""):
            col["personal_data_review"] = True
        columns.append(col)
    warnings = []
    if ragged:
        warnings.append(f"{ragged} row(s) have a different field count than the header")
    return {"rows": rows, "columns": columns, "delimiter": delimiter, "warnings": warnings}


def profile_json(p: Path, fmt: str) -> dict:
    warnings: list[str] = []
    records: list = []
    rows = 0
    if fmt == "jsonl":
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows += 1
                if len(records) < SAMPLE_ROWS:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        warnings.append(f"line {rows}: invalid JSON")
                        break
    else:
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return {"rows": None, "columns": [], "warnings": [f"invalid JSON: {e}"]}
        if isinstance(data, list):
            rows = len(data)
            records = data[:SAMPLE_ROWS]
        elif isinstance(data, dict):
            # common shape: {"data": [...]} or {"items": [...]}
            for key in ("data", "items", "records", "rows", "results"):
                if isinstance(data.get(key), list):
                    rows = len(data[key])
                    records = data[key][:SAMPLE_ROWS]
                    warnings.append(f"records taken from top-level key '{key}'")
                    break
            else:
                rows = 1
                records = [data]
                warnings.append("top-level object, not an array; treated as one record")
    keys: dict[str, list[str]] = {}
    for r in records:
        if isinstance(r, dict):
            for k, v in r.items():
                keys.setdefault(k, []).append("" if v is None else (json.dumps(v) if isinstance(v, (dict, list)) else str(v)))
    columns = []
    for k, vals in keys.items():
        col = {"name": k, "type": guess_type(vals), "description": REQUIRED + ": what this field means"}
        if any(v.startswith("{") or v.startswith("[") for v in vals[:50]):
            col["type"] = "object"
        if PERSONAL_DATA_HINTS.search(k):
            col["personal_data_review"] = True
        columns.append(col)
    return {"rows": rows, "columns": columns, "warnings": warnings}


def role_for(fmt: str, name: str) -> str:
    lname = name.lower()
    if lname.startswith("license") or lname.startswith("licence") or lname in ("copying", "notice"):
        return "license"
    if fmt in DOC_FORMATS:
        return "documentation"
    return "primary"


def collect_inputs(src: Path) -> list[Path]:
    if src.is_file():
        return [src]
    files = []
    for p in sorted(src.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(src).parts):
            files.append(p)
    return files


# ---------- init ----------

def cmd_init(args: argparse.Namespace) -> int:
    src = Path(args.path).expanduser().resolve()
    if not src.exists():
        print(f"error: {src} does not exist", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser().resolve()
    if out.exists() and any(out.iterdir()) and not args.force:
        print(f"error: {out} is not empty (use --force to overwrite)", file=sys.stderr)
        return 2
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    title = args.title or (src.stem if src.is_file() else src.name).replace("_", " ").replace("-", " ").strip().title()
    slug = args.slug or slugify(title)
    inputs = collect_inputs(src)
    if not inputs:
        print("error: no files found", file=sys.stderr)
        return 2

    files_meta = []
    schema = []
    warnings: list[str] = []
    personal_flags: list[str] = []
    total_rows = 0
    base = src if src.is_dir() else src.parent
    for p in inputs:
        rel = p.relative_to(base)
        dest = data_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if p.resolve() != dest.resolve():
            shutil.copy2(p, dest)
        fmt = DATA_EXT.get(p.suffix.lower(), p.suffix.lower().lstrip(".") or "binary")
        entry = {
            "path": str(Path("data") / rel).replace(os.sep, "/"),
            "role": role_for(fmt, p.name),
            "format": fmt,
            "bytes": dest.stat().st_size,
            "sha256": sha256_file(dest),
        }
        prof = None
        if fmt in ("csv", "tsv"):
            prof = profile_csv(dest, fmt)
        elif fmt in ("json", "jsonl"):
            prof = profile_json(dest, fmt)
        if prof:
            if prof.get("rows") is not None:
                entry["rows"] = prof["rows"]
                total_rows += prof["rows"] or 0
            if prof.get("columns"):
                schema.append({"file": entry["path"], "columns": prof["columns"]})
                for c in prof["columns"]:
                    if c.get("personal_data_review"):
                        personal_flags.append(f"{entry['path']}:{c['name']}")
            for w in prof.get("warnings", []):
                warnings.append(f"{entry['path']}: {w}")
        elif fmt in ("parquet", "xlsx", "xls", "sqlite"):
            warnings.append(f"{entry['path']}: schema not inferred for {fmt}; describe columns by hand in manifest.json#structure.schema")
        files_meta.append(entry)

    manifest = {
        "graunt_staging_manifest": MANIFEST_VERSION,
        "slug": slug,
        "title": title,
        "version": dt.date.today().strftime("%Y.%m.%d"),
        "built_at": now_iso(),
        "family": REQUIRED + ": reference-data | document-set | records | geodata | corpus | other",
        "kind": REQUIRED + ": e.g. code-list, calendar, crosswalk, statute-text, registry-snapshot, price-list",
        "summary": REQUIRED + ": one paragraph an agent can read to decide whether this packet answers its need",
        "intended_uses": [REQUIRED + ": a concrete task, e.g. 'validate ISO 3166-1 alpha-2 codes in a form'"],
        "not_intended_for": [REQUIRED + ": what this must not be relied on for, e.g. 'legal advice; the official gazette controls'"],
        "coverage": {
            "temporal": REQUIRED + ": e.g. 2025-01-01 to 2027-12-31",
            "geographic": REQUIRED + ": e.g. worldwide / US federal / Ohio",
            "row_count": total_rows or None,
        },
        "sources": [
            {
                "name": REQUIRED + ": the upstream authority or dataset name",
                "url": REQUIRED + ": where it came from",
                "license": REQUIRED + ": SPDX id of the SOURCE (e.g. CC-BY-4.0, public-domain, proprietary-own-work)",
                "version_or_edition": REQUIRED + ": or 'n/a'",
                "fetched_at": REQUIRED + ": ISO date you obtained it",
                "transformation": REQUIRED + ": what you did to it (none / cleaned / merged / extracted)",
            }
        ],
        "rights": {
            "license_family": REQUIRED + ": SPDX id you GRANT buyers (must be compatible with every source license)",
            "redistribution_allowed": REQUIRED + ": true | false | 'with-attribution' | 'share-alike'",
            "training_allowed": REQUIRED + ": true | false | 'unspecified'",
            "citation_required": REQUIRED + ": true | false",
            "personal_data": REQUIRED + ": 'none' | 'contains-personal-data' | 'pseudonymised' | 'aggregated-only'",
            "notes": REQUIRED + ": the buyer's obligations in one or two plain sentences",
        },
        "structure": {"files": files_meta, "schema": schema},
        "freshness": {
            "as_of": REQUIRED + ": the date the data reflects",
            "refresh_cadence": REQUIRED + ": one-off snapshot | monthly | with upstream releases | ...",
            "known_staleness_risks": REQUIRED + ": what goes out of date first",
        },
        "evidence_refs": {
            "INTEGRITY": ["checksums.sha256", "manifest.json#structure.files[].sha256"],
            "STRUCTURE": ["manifest.json#structure.schema"],
            "FRESHNESS": ["manifest.json#freshness"],
            "RIGHTS": ["RIGHTS.md", "manifest.json#rights"],
            "SAFETY": ["RIGHTS.md#personal-data"],
            "ORIGIN": ["manifest.json#sources"],
            "DELIVERY_MATCH": ["README.md#what-you-get"],
        },
        "limitations": [REQUIRED + ": at least one honest limitation"],
        "citation_template": f"{title}, Graunt packet {slug} v{dt.date.today().strftime('%Y.%m.%d')}, sha256 of file used",
        "_staging": {
            "warnings": warnings,
            "personal_data_review_columns": personal_flags,
            "next_steps": [
                "Replace every REQUIRED value in manifest.json, RIGHTS.md and README.md.",
                "python3 prepare_packet.py validate <this dir>",
                "python3 prepare_packet.py zip <this dir>",
                "Upload at https://graunt.com/prepare, confirm rights and price, submit for review.",
            ],
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    rights_md = f"""# Rights declaration — {title}

These are the seller's declarations. Only declare what you actually hold.
A missing or ambiguous declaration is read as restrictive by buyers and agents.

## Who holds the rights
{REQUIRED}: e.g. "Original compilation by <seller>." or "Public-domain US federal government work, compiled by <seller>."

## License family granted to buyers
{REQUIRED}: SPDX identifier (e.g. CC-BY-4.0) or a short plain-language grant. Must be compatible with every source license below.

## What buyers may do
- Read and use internally: {REQUIRED}: yes / no
- Redistribute the content to their own users: {REQUIRED}: yes / no / with attribution / share-alike
- Use it to train or fine-tune models: {REQUIRED}: yes / no / not addressed by the license (say so)
- Citation required: {REQUIRED}: yes / no — if yes, give the exact attribution line below

## Sources and their licenses
| Source | URL | License | What was taken | Obligations that propagate |
|---|---|---|---|---|
| {REQUIRED} | {REQUIRED} | {REQUIRED} | {REQUIRED} | {REQUIRED} |

## Personal data
{REQUIRED}: "none" / describe what personal data is present, its lawful basis, and any handling obligations.
{('Columns flagged for review: ' + ', '.join(personal_flags)) if personal_flags else 'No column names suggested personal data; confirm by inspecting the rows.'}

## Attribution line (if citation is required)
{REQUIRED}: e.g. "Contains data from <source> (<license>), packaged by <seller> via Graunt."

## License texts
Paste or attach the full text of every source license that requires it (MIT, BSD, Apache, LGPL, CC-BY all do).
"""
    (out / "RIGHTS.md").write_text(rights_md, encoding="utf-8")

    files_table = "\n".join(f"| `{f['path']}` | {f['format']} | {f.get('rows', '')} | {f['bytes']:,} |" for f in files_meta)
    readme = f"""# {title}

{REQUIRED}: two or three sentences. What this is, who it is from, and the single most useful thing an agent can do with it.

## What you get
| File | Format | Rows | Bytes |
|---|---|---|---|
{files_table}

Schema and content hashes are in `manifest.json`; checksums in `checksums.sha256`.

## What it helps an agent do
- {REQUIRED}: a concrete task, phrased as the agent would receive it
- {REQUIRED}: another

## Coverage
{REQUIRED}: temporal and geographic coverage, and what is deliberately excluded.

## Known limitations
- {REQUIRED}: at least one honest limitation

## Freshness
As of {REQUIRED}: date. Refresh: {REQUIRED}: cadence.

## Citation
{REQUIRED}: the line a reader should use to cite this packet, including version and hash.
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    write_checksums(out)

    print(f"Staged {len(files_meta)} file(s) into {out}")
    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")
    if personal_flags:
        print("Columns that look like personal data (review before listing):")
        for c in personal_flags:
            print(f"  - {c}")
    print("Now replace every REQUIRED value in manifest.json, RIGHTS.md and README.md, then run:")
    print(f"  python3 {Path(__file__).name} validate {out}")
    return 0


def write_checksums(out: Path) -> None:
    lines = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name not in ("checksums.sha256", "manifest.json"):
            lines.append(f"{sha256_file(p)}  {p.relative_to(out).as_posix()}")
    (out / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------- validate ----------

def find_required(obj, path="manifest.json") -> list[str]:
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "_staging":
                continue
            hits += find_required(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += find_required(v, f"{path}[{i}]")
    elif isinstance(obj, str) and obj.startswith(REQUIRED):
        hits.append(path)
    return hits


def cmd_validate(args: argparse.Namespace) -> int:
    d = Path(args.dir).expanduser().resolve()
    problems: list[str] = []
    mpath = d / "manifest.json"
    if not mpath.exists():
        print(f"error: {mpath} missing", file=sys.stderr)
        return 2
    try:
        m = json.loads(mpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: manifest.json is not valid JSON: {e}", file=sys.stderr)
        return 2

    problems += [f"placeholder left at {p}" for p in find_required(m)]
    for name in ("RIGHTS.md", "README.md"):
        p = d / name
        if not p.exists():
            problems.append(f"{name} missing")
        elif PLACEHOLDER_RE.search(p.read_text(encoding="utf-8", errors="replace")):
            problems.append(f"placeholder left in {name}")

    rights = m.get("rights", {})
    if rights.get("redistribution_allowed") not in ALLOWED_REDIST:
        problems.append("rights.redistribution_allowed must be true, false, 'with-attribution', 'share-alike' or 'unspecified'")
    if rights.get("training_allowed") not in ALLOWED_TRI:
        problems.append("rights.training_allowed must be true, false or 'unspecified'")
    if not isinstance(rights.get("citation_required"), bool):
        problems.append("rights.citation_required must be true or false")
    if rights.get("personal_data") not in ALLOWED_PERSONAL:
        problems.append("rights.personal_data must be one of " + ", ".join(sorted(ALLOWED_PERSONAL)))
    if not isinstance(rights.get("license_family"), str) or not rights.get("license_family").strip():
        problems.append("rights.license_family must be a non-empty SPDX id or plain grant")
    if not m.get("sources"):
        problems.append("sources must list at least one source")

    files = m.get("structure", {}).get("files", [])
    if not files:
        problems.append("structure.files is empty")
    for f in files:
        p = d / f.get("path", "")
        if not p.exists():
            problems.append(f"{f.get('path')} listed in manifest but missing on disk")
            continue
        actual = sha256_file(p)
        if actual != f.get("sha256"):
            problems.append(f"{f['path']}: sha256 mismatch (manifest {str(f.get('sha256'))[:12]}…, file {actual[:12]}…)")
        if p.stat().st_size != f.get("bytes"):
            problems.append(f"{f['path']}: size mismatch")
        if f.get("format") in ("csv", "tsv") and "rows" in f:
            prof = profile_csv(p, f["format"])
            if prof["rows"] != f["rows"]:
                problems.append(f"{f['path']}: row count mismatch (manifest {f['rows']}, file {prof['rows']})")
    # checksums file
    cpath = d / "checksums.sha256"
    if not cpath.exists():
        problems.append("checksums.sha256 missing")
    else:
        for line in cpath.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, _, rel = line.partition("  ")
            p = d / rel
            if not p.exists():
                problems.append(f"checksums.sha256 names missing file {rel}")
            elif sha256_file(p) != digest:
                problems.append(f"checksums.sha256 stale for {rel} (run: prepare_packet.py checksums <dir>)")
    # schema descriptions
    for s in m.get("structure", {}).get("schema", []):
        for c in s.get("columns", []):
            if str(c.get("description", "")).startswith(REQUIRED):
                problems.append(f"{s['file']}: column '{c['name']}' has no description")
                break

    if problems:
        print(f"NOT READY — {len(problems)} problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("READY. Next:")
    print(f"  python3 {Path(__file__).name} zip {d}")
    print("  Upload at https://graunt.com/prepare (drafts stay private until you publish).")
    return 0


# ---------- checksums ----------

def cmd_checksums(args: argparse.Namespace) -> int:
    d = Path(args.dir).expanduser().resolve()
    if not (d / "manifest.json").exists():
        print(f"error: {d} has no manifest.json", file=sys.stderr)
        return 2
    write_checksums(d)
    print(f"wrote {d / 'checksums.sha256'}")
    return 0


# ---------- zip ----------

def cmd_zip(args: argparse.Namespace) -> int:
    d = Path(args.dir).expanduser().resolve()
    slug = d.name
    try:
        slug = json.loads((d / "manifest.json").read_text(encoding="utf-8")).get("slug", slug)
    except Exception:
        pass
    out = Path(args.out).expanduser().resolve() if args.out else d.parent / f"{slug}.zip"
    fixed = (2026, 1, 1, 0, 0, 0)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                zi = zipfile.ZipInfo(f"{slug}/{p.relative_to(d).as_posix()}", date_time=fixed)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                z.writestr(zi, p.read_bytes())
    digest = sha256_file(out)
    (out.with_suffix(out.suffix + ".sha256")).write_text(f"{digest}  {out.name}\n", encoding="utf-8")
    print(f"{out}  {out.stat().st_size:,} bytes  sha256 {digest}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("init", help="stage files into a packet bundle")
    a.add_argument("path")
    a.add_argument("--out", default="./graunt-packet")
    a.add_argument("--title")
    a.add_argument("--slug")
    a.add_argument("--force", action="store_true")
    a.set_defaults(fn=cmd_init)
    v = sub.add_parser("validate", help="check a bundle is complete and consistent")
    v.add_argument("dir")
    v.set_defaults(fn=cmd_validate)
    c = sub.add_parser("checksums", help="re-generate checksums.sha256 after editing README/RIGHTS")
    c.add_argument("dir")
    c.set_defaults(fn=cmd_checksums)
    z = sub.add_parser("zip", help="deterministic zip of a bundle")
    z.add_argument("dir")
    z.add_argument("--out")
    z.set_defaults(fn=cmd_zip)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
