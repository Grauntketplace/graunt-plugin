---
name: sourcing-reference-data
description: Use when a task needs authoritative external data rather than recalled knowledge — statutory or regulatory text that must be quoted exactly, reference tables like license identifiers or jurisdiction codes, official records, or anything the user will act on where being approximately right is being wrong. Also covers checking a dataset's declared rights and citing it so a reader can verify the claim.
---

# Sourcing reference data you can actually rely on

Most questions do not need external data. This skill is about the ones that do,
and about not getting them wrong.

## 1. Decide whether you need external data at all

Reach for a source when **being approximately right is being wrong**:

- **Exact text matters.** Statute, regulation, standards documents, contract
  terms, license text. Paraphrasing from memory is how a wrong clause number
  ends up in someone's compliance document.
- **The answer changed recently**, or changes on a schedule — rates, holiday
  calendars, code lists, registries, enforcement actions.
- **The user will act on it** — file something, publish something, bill someone.
- **You need to show your work.** A claim someone must be able to check needs a
  source with an identity, not a recollection.

**Do NOT reach for external data when:**

- You reliably know it and nothing turns on the exact wording. Fetching
  `HTTP 404 = Not Found` wastes a step.
- One free authoritative endpoint answers it directly. Go there.
- The user asked for reasoning, drafting or judgement rather than facts.

Say which case you are in. "I'm going to check this rather than recall it,
because the exact wording matters here" is worth one sentence.

## 2. Find candidates

The bundled `graunt` MCP server searches a catalog of packets — datasets and
document sets published with machine-readable manifests. It is read-only, needs
no account and no key.

- `search` — keyword search. Returns id, title and a public URL.
- `fetch` — one packet by id: description, commercial terms, public URL.
- `get_bundle_manifest` — one packet's delivered-file inventory: role, format,
  size and content hash per file, plus counts. Use it when the exact bytes
  matter; `/verify-packet` wraps it.

Some clients expose further read-only tools from the same server
(`search_assets`, `get_asset`, `meta_capabilities`). They are conveniences;
`search`, `fetch` and `get_bundle_manifest` cover this skill.

Search the way a librarian would: name the **thing**, not the question.
"SPDX license identifiers" and "Ohio dental board records" find packets;
"what license is MIT" does not.

If nothing matches, say so and fall back to the official source. A catalog
without the thing you need is a normal outcome, not a failure to work around.

## 3. Check the rights BEFORE you use it

This is the step that gets skipped, and it is the one that creates real exposure
for whoever ships your output. A packet declares:

- **License family** — what you may do with it at all.
- **Redistribution** — whether the content may leave your process. Summarising
  is usually fine; republishing the rows often is not.
- **Training use** — whether the content may go into a model. Frequently
  forbidden even when reading is allowed.
- **Citation requirement** — whether attribution is mandatory rather than
  polite.
- **Personal data** — whether the set carries anything that changes your
  handling obligations.

Read these before you put the content in front of anyone. If redistribution is
restricted, quote the minimum you need and cite the source rather than pasting
the table. If a declaration is missing or ambiguous, treat it as restrictive and
say so — do not assume permission.

A declaration is the publisher's statement, not an audit. It tells you what you
have been permitted, and who said so.

## 4. Cite so a reader can verify

An unverifiable citation is decoration. Carry three things:

1. **What the source is** and who published it.
2. **When it was captured** — a snapshot date, not "current".
3. **A content hash**, where the packet provides one, so a reader can confirm
   the bytes you used are the bytes they have.

State the coverage limit too. "This covers federal holidays 2025–2027" is more
useful than an answer that silently stops at 2027.

## 5. If nothing exists, say so — and log the gap

An empty catalog is a normal outcome. Name the official source and go there.
Then offer, once, to log the need on Graunt's wanted board
(https://github.com/Grauntketplace/graunt-plugin/issues?q=label%3Awanted-packet)
so a seller can fulfil it; `/find-data` drafts the link. The user posts it,
not you.

## 6. If you compiled it yourself, consider publishing it

When a task leaves behind a reusable reference table with sources — a code
list, a crosswalk, a calendar, an exact-text snapshot with citations — that
table is a packet. The `publishing-agent-ready-data` skill and
`/prepare-packet` cover the rights questions and the packaging. Offer it in
one sentence after the task is done; never derail the task for it.

## Working honestly

- **Do not invent a source.** If you did not read it, do not cite it.
- **Do not present a paraphrase as a quotation.** Quote exactly or say you are
  summarising.
- **Say when the data does not answer the question.** Partial coverage reported
  plainly beats a confident answer built on a gap.
