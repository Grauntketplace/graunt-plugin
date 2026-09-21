# Graunt — agent-ready data

[Graunt](https://graunt.com) is a marketplace for data that agents can trust:
packets published with machine-readable manifests, **declared rights**
(license family, redistribution, training use, citation, personal data),
provenance and content hashes. The catalog is public and free to search. This
repository is the Claude Code plugin, the install hub for the MCP connector in
every other client, and the public board where people who need data meet
people who have it.

- **Buyers and agents:** find reference data you can cite, check its rights
  before you use it, verify the bytes you downloaded.
- **Sellers:** turn a file or folder into a review-ready packet in about ten
  minutes with `/prepare-packet`. Listing is free.
- **Both:** the [wanted / offered board](#the-board-wanted-and-offered-packets).

## Install

The connector is read-only, needs no account and no key, cannot see anything
you own, and cannot spend money. It serves the same catalog `graunt.com`
serves any visitor.

### Claude Code (plugin: skills, commands and the connector)

```text
/plugin marketplace add Grauntketplace/graunt-plugin
/plugin install graunt@graunt
```

Connector only, no skills:

```bash
claude mcp add --transport http graunt https://api.graunt.com/v1/mcp
```

### Claude.ai and Claude Desktop

Settings → Connectors → **Add custom connector** → URL
`https://api.graunt.com/v1/mcp` → Add. (Custom connectors need a Pro, Max,
Team or Enterprise plan.)

### Cursor

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=graunt&config=eyJ1cmwiOiJodHRwczovL2FwaS5ncmF1bnQuY29tL3YxL21jcCJ9)

Or paste this deeplink into a browser: `cursor://anysphere.cursor-deeplink/mcp/install?name=graunt&config=eyJ1cmwiOiJodHRwczovL2FwaS5ncmF1bnQuY29tL3YxL21jcCJ9`
— or add to `.cursor/mcp.json`:

```json
{ "mcpServers": { "graunt": { "url": "https://api.graunt.com/v1/mcp" } } }
```

### VS Code

[Install in VS Code](https://vscode.dev/redirect/mcp/install?name=graunt&config=%7B%22type%22%3A%22http%22%2C%22url%22%3A%22https%3A%2F%2Fapi.graunt.com%2Fv1%2Fmcp%22%7D)
— or from a terminal:

```bash
code --add-mcp '{"name":"graunt","type":"http","url":"https://api.graunt.com/v1/mcp"}'
```

### ChatGPT

Settings → Apps & Connectors → Advanced settings → **Developer mode** on →
Create → paste `https://api.graunt.com/v1/mcp`. (Plus, Pro, Business,
Enterprise and Edu plans.)

### Other clients

| Client | Where | Snippet |
|---|---|---|
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | `{ "mcpServers": { "graunt": { "serverUrl": "https://api.graunt.com/v1/mcp" } } }` |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.graunt]`<br>`url = "https://api.graunt.com/v1/mcp"` |
| Gemini CLI | terminal | `gemini mcp add --transport http graunt https://api.graunt.com/v1/mcp` |
| Any stdio-only client | its MCP config | command `npx`, args `["-y", "mcp-remote", "https://api.graunt.com/v1/mcp"]` |

Graunt is also listed in the official
[MCP registry](https://registry.modelcontextprotocol.io) as
`com.graunt/marketplace`, so clients that browse the registry can add it from
there.

## What the plugin does

### For buyers and the agents they run

- **Skill `sourcing-reference-data`** — activates when a task needs an
  authoritative source rather than recalled knowledge: statutory or regulatory
  text quoted exactly, code lists, official records, anything the user will act
  on. It also covers when *not* to fetch anything, and treats a missing or
  ambiguous rights declaration as restrictive.
- **`/find-data <thing>`** — search the catalog and report each candidate
  with its rights alongside it. When nothing matches it names the official
  source and offers to log the need on the board.
- **`/check-rights <packet>`** — what a packet's declared rights permit, for
  the use you actually have in mind.
- **`/verify-packet <packet> [files]`** — pull the packet's file inventory and
  content hashes from the catalog and compare against what you downloaded.

### For sellers

- **Skill `publishing-agent-ready-data`** — when something you own or just
  compiled is worth listing, how to declare rights honestly (only what you
  hold; source licenses propagate), what makes a packet agent-ready, and how
  to price it.
- **`/prepare-packet <path>`** — stages your files, computes SHA-256 per
  file, infers CSV/JSON schemas and row counts, flags columns that look like
  personal data, and writes `manifest.json`, `RIGHTS.md`, `README.md` and
  `checksums.sha256` with every field you must supply marked `REQUIRED`. Its
  validator refuses to pass until nothing is left blank and every hash
  matches. Then you upload the bundle at
  [graunt.com/prepare](https://graunt.com/prepare); Graunt drafts the
  listing, you confirm rights and price, and nothing is public until review
  passes. The script is plain Python with no dependencies:
  [`scripts/prepare_packet.py`](scripts/prepare_packet.py).

### Seed packets: worked examples you can use or list

[`packets/`](packets/) contains a reproducible build for free, rights-clean
reference packets (public holidays, ISO code lists, SPDX license data, IANA
time zones, US states and more), each with a manifest, checksums and a rights
file in the format above. Built bundles are attached to the
[releases](https://github.com/Grauntketplace/graunt-plugin/releases). Use them
as data, as templates for your own packets, or list improved versions.

## The board: wanted and offered packets

The [issue tracker](https://github.com/Grauntketplace/graunt-plugin/issues) is
Graunt's public matching board.

- **Need data?** Open a
  [wanted-packet issue](https://github.com/Grauntketplace/graunt-plugin/issues/new?template=wanted-packet.yml).
  Say what task it serves, the coverage and freshness you need, the rights
  you need, and whether you would pay. Sellers watch the
  [`wanted-packet`](https://github.com/Grauntketplace/graunt-plugin/issues?q=is%3Aissue+is%3Aopen+label%3Awanted-packet)
  label.
- **Have data?** Comment `claiming` on a wanted issue, or open an
  [offered-packet issue](https://github.com/Grauntketplace/graunt-plugin/issues/new?template=packet-offered.yml).
  Then list it with `/prepare-packet` or at graunt.com/prepare.
- **[WANTED.md](board/WANTED.md)** holds the specs Graunt seeded from what
  agents ask this plugin for, each with a one-click link to open it as an
  issue. The first listing that meets a spec is featured here and in
  `/find-data` results for that topic (see `board/featured.json`).

## About the rights fields

Packets on Graunt carry declared rights — license family, redistribution,
training use, citation requirement, personal data. These are the
**publisher's declarations**, not an audit and not legal advice. The skills
treat a missing or ambiguous declaration as restrictive rather than assuming
permission, on both the buying and the selling side.

## The connector, for reference

```json
{
  "mcpServers": {
    "graunt": {
      "url": "https://api.graunt.com/v1/mcp",
      "transport": "http"
    }
  }
}
```

Tools: `search` (keyword search over the catalog), `fetch` (one packet by id:
description, commercial terms, public URL) and `get_bundle_manifest` (a
packet's delivered-file inventory with content hashes). Some clients also
expose `search_assets`, `get_asset` and `meta_*` conveniences from the same
server. Everything is read-only.

## What Graunt is

A marketplace for agent-ready data: datasets and document sets published with
machine-readable manifests, declared rights, provenance and content hashes,
discoverable by agents over MCP and a JSON API. The catalog is public and free
to search; some packets are free and some are paid; listing is free. Read more
at [graunt.com/sell](https://graunt.com/sell) and
[graunt.com/api-agents](https://graunt.com/api-agents).

## License

MIT. See [LICENSE](LICENSE). Seed packets carry their own rights files.
