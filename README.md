# Graunt — agent-ready data

A Claude plugin for **sourcing external reference data you can rely on**:
deciding when a task needs an authoritative source rather than recalled
knowledge, finding one, checking its declared rights before you use it, and
citing it so a reader can verify the claim.

## What's in it

- **Skill — `sourcing-reference-data`.** Activates when a task needs
  authoritative data: statutory or regulatory text that must be quoted exactly,
  reference tables, official records, or anything the user will act on. It also
  covers when *not* to fetch anything.
- **`/find-data`** — search for data matching a described need, with each
  candidate's rights reported alongside it.
- **`/check-rights`** — what a packet's declared rights permit, for a specific
  intended use.
- **MCP connector** — a read-only server over Graunt's public catalog. No
  account, no key.

## The connector

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

It exposes `search` and `fetch` over the published catalog and nothing else —
the same data `graunt.com` serves any visitor. It carries no credential, cannot
see anything you own, and cannot spend money. Graunt is also listed in the
[MCP registry](https://registry.modelcontextprotocol.io) as
`com.graunt/marketplace`.

## About the rights fields

Packets on Graunt carry declared rights — license family, redistribution,
training use, citation requirement, personal data. These are the **publisher's
declarations**, not an audit and not legal advice. The skill treats a missing or
ambiguous declaration as restrictive rather than assuming permission.

## What Graunt is

A marketplace for agent-ready data: datasets and document sets published with
machine-readable manifests, declared rights and content hashes. The catalog is
public and free to search; some packets are free and some are paid. This plugin
searches and inspects — it does not buy anything.

## License

MIT. See [LICENSE](LICENSE).
