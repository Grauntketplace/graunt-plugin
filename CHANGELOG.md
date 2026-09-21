# Changelog

## 0.2.0 — 2026-09-21

### Fixed
- The bundled MCP server never loaded in Claude Code: `.mcp.json` used
  `"transport": "http"` and Claude Code requires `"type": "http"`, so the plugin
  installed without its connector. Anyone who installed 0.1.0 should update.

### Added — distribution
- `.claude-plugin/marketplace.json`: the repository is now a plugin marketplace.
  `/plugin marketplace add Grauntketplace/graunt-plugin` then
  `/plugin install graunt@graunt`.
- README install hub with one-line configs for Claude Code, Claude.ai and
  Claude Desktop, Cursor, VS Code, ChatGPT, Windsurf, Codex CLI, Gemini CLI and
  stdio-only clients via `mcp-remote`.

### Added — seller side
- Skill `publishing-agent-ready-data`: recognising a packet, deciding whether
  to list it, declaring rights honestly (unknown is restrictive; source
  licences propagate), making it agent-ready, pricing, hand-off.
- `/prepare-packet <path>` and `scripts/prepare_packet.py` (stdlib only):
  inventory, SHA-256 per file, CSV/JSON schema and row counts, personal-data
  column flags, `manifest.json` / `RIGHTS.md` / `README.md` / `checksums.sha256`
  skeletons with `REQUIRED` markers, a validator that refuses to pass until the
  bundle is complete and consistent, and a deterministic zip.
- `packets/`: reproducible build of free, rights-clean seed packets from pinned
  PyPI and npm sources, each with manifest, checksums and rights file. Built
  bundles ship as release assets.

### Added — buyer side
- `/verify-packet`: pull a packet's file inventory and content hashes with
  `get_bundle_manifest` and compare against local files.
- `/find-data`: when nothing matches, name the official source and offer a
  prefilled wanted-packet link; check `board/featured.json`.
- Sourcing skill documents `get_bundle_manifest`, the wanted board, and the
  "you just compiled a packet" nudge.

### Added — the board
- Issue templates for `wanted-packet` and `packet-offered`.
- `board/wanted.json` with 23 seeded specs, `board/WANTED.md` with one-click
  open-as-issue links, `board/featured.json`, and `scripts/board.py`
  (`render`, `urls`, `seed` via `gh`).

## 0.1.0 — 2026-09-03

- Initial release: `sourcing-reference-data` skill, `/find-data`,
  `/check-rights`, bundled read-only MCP connector.
