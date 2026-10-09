# td-segment-optimizer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.8+](https://img.shields.io/badge/python-3.8+-brightgreen.svg)](https://www.python.org/)
[![Vibe Coded](https://img.shields.io/badge/vibe%20coded-with%20AI-blueviolet.svg)](#vibe-coded)
[![GitHub Pages](https://img.shields.io/badge/docs-GitHub%20Pages-indigo.svg)](https://anilkulkarni87.github.io/td-segment-optimizer/)

> ⚡ **Built with Vibe Coding**: This tool was 100% vibe coded — conceptualized, pair-programmed, designed, and tested in a continuous, fast-paced flow with an AI pair programmer to eliminate friction in Treasure Data CDP audience management.

Find out which **Treasure Data CDP parent segment attributes, behaviors and behavior columns are actually used** by your segments and activations, and which are not.

Parent segments tend to accumulate attributes over time. Unused attributes slow down builds, add PII exposure, and clutter the segment builder. This tool gives you an evidence-based clean-up list in seconds.

- ✅ **Fast Incremental Mode**: 10–25s refreshes after initial baseline
- ✅ **Zero Dependencies**: Pure Python 3.8+ standard library
- ✅ **Interactive Local Dashboard**: Every audit generates a self-contained `report.html` with instant filtering, search, and CSV export
- ✅ **Multi-Region**: Works with any TD region (`us01`, `eu01`, `ap01`, `ap02`)
- ✅ **Zero PII**: Read-only `GET` queries, never modifies accounts or logs credentials
- ✅ **Interactive Docs & Demo**: Hosted on GitHub Pages with live interactive samples

## What it reports

| Output | Content |
|---|---|
| `report.html` | **Interactive offline dashboard**: instant text search, status & kind filtering, dynamic KPI cards, integrity warnings, and filtered CSV export (zero runtime dependencies) |
| `report.md` | Human-readable audit: summary, never-used attributes by group, ranked usage, behaviors, integrity findings |
| `attribute_usage.csv` | One row per attribute / behavior / behavior column with `segments`, `segments_recent`, `activations`, `last_used`, `status` |
| `summary.json` | Machine-readable totals + integrity findings |
| raw `*.json` | API responses, so you can re-analyze offline |

Status values: `active`, `dormant` (only used by segments not edited within `--recent-days`),
`activation_only` (exported but never filtered on), `never_used`.

Integrity findings:
- segment rules referencing **attributes that no longer exist** on the parent segment
- segment rules referencing **unknown behaviors**
- segments that include or exclude **deleted segments**

See [`examples/sample_report.html`](examples/sample_report.html) and [`examples/sample_report.md`](examples/sample_report.md) (generated from synthetic data).

## Quick start

```bash
git clone https://github.com/anilkulkarni87/td-segment-optimizer.git && cd td-segment-optimizer
export TD_API_KEY=xxxx/xxxxxxxx           # or rely on ~/.td/td.conf

# 1. find your parent segment id
python3 scripts/td_cdp_audit.py list-parents --region eu01

# 2. initial audit (full baseline)
python3 scripts/td_cdp_audit.py run -p 123456 --region eu01 --include-activations -o ./td_cdp_audit_123456

# 3. subsequent audit (fast incremental: only downloads new/modified segments and activations)
python3 scripts/td_cdp_audit.py run -p 123456 --include-activations -o ./td_cdp_audit_123456 --since-last-run

# 4. re-analyze offline with a specific cutoff date
python3 scripts/td_cdp_audit.py analyze -i ./td_cdp_audit_123456 --since 2026-10-01
```

### State Storage & Incremental Cache
Where does the tool store state?
By default, the state is saved in `./td_cdp_audit_<parent_segment_id>/` (or whatever path you pass to `-o / --out`). 

For incremental runs to work, **you must preserve this directory and point `-o` to the same folder on subsequent runs**:

| File in State Directory | Purpose for Incremental Runs |
|---|---|
| `fetch_meta.json` | Stores `fetched_at` and `previous_fetched_at` timestamps, endpoint, and delta statistics. |
| `segments.json` | Caches all segment rules. Only new or modified segments (`updatedAt > fetched_at`) are fetched on future runs. |
| `activations.json` | Caches activation column mappings. Reused so thousands of syndication API calls are avoided. |
| `audience.json` | Caches parent segment attribute and behavior catalog. |
| `customers_schema.json` | Caches the matrix table schema to classify unmapped rule fields. |

> **Privacy Note**: Because the state directory contains customer metadata (segment names, creator names), it is automatically ignored by `.gitignore` (`td_cdp_audit_*/`) so you never accidentally commit it to a public repository. If running in a pipeline or recurring cron, persist this folder across runs (e.g. via CI cache, local disk, or an internal private bucket).


### Authentication

Resolved in this order. The key is never printed or saved.
1. Environment variable `TD_API_KEY` (change the name with `--api-key-env`)
2. `~/.td/td.conf`, the file the `td` CLI writes. Pick a section with `--profile` (default `account`).

The key needs read access to Audience Studio for the parent segment. A `404 Record not found`
on a parent segment you can see in the UI usually means the key belongs to a user without
access to it.

### Endpoint

`--endpoint URL` › `--region` › `$TD_REGION` › `endpoint` in td.conf (rewritten from `api.` to
`api-cdp.`) › `us01`.

### Performance

| Parent segment size | Typical runtime |
|---|---|
| < 200 segments | < 1 min |
| 1,000–2,000 segments | 5–10 min (the segment list endpoint is slow) |

`--include-activations` adds one request per segment that has activations (8 run in parallel
by default; change with `--workers`).

## Strategic Applications: Beyond Attribute Cleanup

While this tool was created to slim down parent segments and prune unused database columns, the raw extracts (`segments.json`, `activations.json`, `audience.json`, `summary.json`) represent a complete machine-readable snapshot of an organization's marketing operations.

Teams, data architects, and AI workflows can leverage these JSON extracts to unlock continuous operational intelligence:

### 1. Quantify Data Product & Feature Adoption (`audience.json` + `segments.json`)
Data engineering teams invest heavily in complex behavioral tables (e.g. recency/frequency metrics, product interaction logs) and predictive machine learning models (churn risk, propensity scores, LTV tiers). By cross-referencing `audience.json` against actual filter usage in `segments.json`, engineering leaders can measure which data products actively drive campaigns versus which represent untouched operational overhead.

### 2. Discover Segmentation Patterns & Auto-Generate Templates (`segments.json`)
`segments.json` exports the complete Abstract Syntax Tree (AST) of all production segment filters. Internal tools and AI agents can analyze recurring filter combinations, recency windows, and operator patterns to synthesize high-converting segment templates and accelerate operator onboarding.

### 3. Semantic Audience Deduplication & Governance (`segments.json`)
Over years of marketing cycles, CDPs accumulate hundreds of overlapping audiences. Parsing the filter trees enables similarity scoring to identify near-duplicate audience definitions, merge redundant segment jobs, and prevent conflicting messaging to the same customer.

### 4. Audit Outbound Lineage & PII Egress (`activations.json`)
`activations.json` indexes every syndication connector (ESPs, ad networks, cloud object storage, webhooks) along with payload field mappings. Compliance and security teams gain an automated audit log of which customer identifiers leave the CDP, validating consent compliance and enforcing data minimization principles.

### 5. Upstream Data Warehouse Optimization (`attribute_usage.csv` + `summary.json`)
Usage classifications (`never_used`, `dormant`, `activation_only`) provide factual evidence for data platform teams to safely drop unneeded golden-layer columns in Snowflake, Databricks, BigQuery, or Redshift—accelerating daily CDP matrix builds and slashing scan costs.

### 6. Continuous CI/CD Guardrails & Breakage Detection (`summary.json`)
Running automated audits in CI/CD or Digdag schedules surfaces broken segment references, missing behavior tables, and unmapped rule fields before scheduled syndications fail in production, protecting critical revenue-generating campaigns.

## Using it as an AI agent skill

`SKILL.md` follows the common *skills* layout (YAML front-matter + instructions) used by
Antigravity / Gemini, Claude Code and similar agents. Copy or symlink this folder into your
agent's skills directory, for example:

```bash
ln -s "$PWD" ~/.gemini/config/skills/td-cdp-attribute-usage-audit     # Antigravity
ln -s "$PWD" ~/.claude/skills/td-cdp-attribute-usage-audit            # Claude Code
```

Then ask: *"Which attributes in parent segment 123456 have never been used in a segment?"*

## Limitations

Usage is derived from **segment rules** and, if fetched, **activation column mappings**. Not
covered: Journeys, predictive models, workflows/SQL reading `cdp_audience_<id>` tables
directly, and Profiles API consumers. Read
[`references/interpreting_results.md`](references/interpreting_results.md) before deleting anything.

## Development

```bash
python3 -m unittest discover -s tests -v
```

Tests use only synthetic fixtures in `tests/fixtures/`. **Never commit real audit output.**
`.gitignore` excludes `td_cdp_audit_*/` by default.

## License

MIT. See [LICENSE](LICENSE). Not affiliated with or endorsed by Treasure Data, Inc.
