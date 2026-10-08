---
name: td-cdp-attribute-usage-audit
description: >-
  Audits a Treasure Data CDP parent segment (audience) to find which attributes,
  behaviors and behavior columns are used by child segments and activations, and
  which have never been used. Use when the user asks which parent segment attributes
  are unused, wants to clean up / slim down a parent segment, asks how segments are
  built, wants attribute adoption metrics, or wants to find segments referencing
  deleted attributes. Works for any TD account and region via the CDP REST API.
---

# TD CDP Attribute Usage Audit

Answers: *"Which attributes in parent segment X have never been used in any segment?"*

All logic lives in a dependency-free CLI, [`scripts/td_cdp_audit.py`](scripts/td_cdp_audit.py)
(Python 3.8+ stdlib only). Prefer running it over hand-rolled API calls or per-segment MCP
calls — MCP `list_segments` returns names only, and calling `get_segment` once per segment
does not scale past a few dozen segments.

## Steps

1. **Pick the parent segment.** If the user did not give an ID, either call the
   `treasuredata` MCP `list_parent_segments` tool or run:
   ```bash
   python3 scripts/td_cdp_audit.py list-parents [--region eu01] [--profile NAME]
   ```
2. **(Optional) Show a few samples first** if the user wants to understand how segments
   are built: MCP `get_segment` on 2–3 recent segment IDs. Summarise the rule shape
   (attribute conditions, behavior aggregations, `Reference` to other segments).
   See [references/rule_schema.md](references/rule_schema.md).
3. **Run the audit** (fetch + analyze):
   - **First run (baseline)**: downloads full configuration, segment rules, and activations.
     ```bash
     python3 scripts/td_cdp_audit.py run -p <PARENT_SEGMENT_ID> \
         -o <OUTPUT_DIR> --include-activations [--region eu01] [--profile NAME]
     ```
   - **Subsequent runs (incremental)**: point `-o` to the same `<OUTPUT_DIR>`. The tool automatically runs in incremental mode, downloading rules and activations **only for new and modified segments** (`updatedAt > last_run`), reusing cached data for unchanged segments. Add `--since-last-run` to include an incremental delta report:
     ```bash
     python3 scripts/td_cdp_audit.py run -p <PARENT_SEGMENT_ID> \
         -o <OUTPUT_DIR> --include-activations --since-last-run
     ```
     To force a clean redownload from scratch, pass `--full-refresh`.
4. **Re-analyze offline** (no API calls, fast):
   ```bash
   python3 scripts/td_cdp_audit.py analyze -i <OUTPUT_DIR> --since 2026-10-01
   ```
5. **Validate**: stderr ends with `attributes never used: N/M ...` and `<OUTPUT_DIR>`
   contains `report.md`, `attribute_usage.csv`, `summary.json`. Check
   `summary.json → summary.segments_fetch_errors` is 0.
6. **Present results** from `report.md`: totals, never-used attributes grouped by
   `groupingName`, unused behaviors, and the *Integrity findings* section. Read
   [references/interpreting_results.md](references/interpreting_results.md) before
   recommending removals.

## Rules

- Never print, log, or write the API key. The script reads it in-memory only.
- Output folders contain customer metadata (segment names, creator names). Do not commit
  them to public repos; they are ignored by the repo `.gitignore` (`td_cdp_audit_*/`).
- "Never used" is scoped to segment rules (+ activations if fetched). Always mention the
  caveats in the report before suggesting attributes be deleted.
