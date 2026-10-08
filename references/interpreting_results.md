# Interpreting the audit

## Status values (`attribute_usage.csv → status`)

| Status | Meaning | Typical action |
|---|---|---|
| `active` | Used by ≥1 segment updated within `--recent-days` | Keep |
| `dormant` | Used only by segments not edited within the window | Check if those segments are still activated/scheduled |
| `activation_only` | Exported by an activation but never used as a filter | Keep (removing breaks the export) |
| `never_used` | No segment rule and no activation references it | Candidate for removal or promotion |

## Before recommending removal

1. **Activations** — if the run did not use `--include-activations`, `never_used` may be
   wrong for exported fields (e.g. names, first-party IDs used as primary keys).
2. **Other consumers** not visible to this tool:
   - Journeys / journey stages and decision points
   - Predictive scoring models and their feature sets
   - Workflows or SQL that query `cdp_audience_<id>.customers` directly
   - Profiles API / real-time personalization, LLM/agent features
3. **Grouped families** — attributes often arrive in families (e.g. `*_first_seen`,
   `*_last_seen`, `*_91d`, `*_dt` variants). If one member is used, consider keeping the
   family consistent rather than removing a single column.
4. **Duplicates** — look for near-identical columns (`*_tm`, `*_ts`, `*_unixtime` vs date
   string). Usage usually concentrates on one variant; the others are good removal targets.
5. **PII minimisation** — never-used PII attributes (name, phone, street address) are strong
   removal candidates from a privacy standpoint even if cheap to keep.

## Integrity findings

- `unknown_or_removed` rule field → the segment filters on something that no longer exists
  in the parent segment. The segment may error at build time or silently return a different
  audience. Open it in the UI and fix or archive it.
- `matrix_column` → valid; the field comes from the master table rather than an attribute.
- Broken references → a segment includes/excludes a deleted segment.

## Benefits worth quoting

- Faster parent segment builds and lower storage (fewer joined columns × population).
- A cleaner segment builder UI for marketers, so it's easier to pick the right field.
- A smaller PII footprint.
