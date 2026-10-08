# CDP Attribute Usage Audit — Parent Segment 100 (Acme Outdoor - Master Audience)

Generated 2026-10-07 by td-cdp-attribute-usage-audit v1.0.0. Analyzed **4 segments** (3 with rules) and **1 activations**.

Status legend: `never_used` (no segment rule or activation) · `activation_only` (exported but never filtered on) · `dormant` (used only by segments not updated in the last 180 days) · `active`.

## Summary

| Object | Total | Active | Dormant | Activation only | Never used |
|---|---|---|---|---|---|
| Attributes | 7 | 2 | 1 | 1 | **3** (42%) |
| Behaviors | 2 | 1 | 0 | 0 | **1** (50%) |
| Behavior columns | 4 | 2 | 0 | 0 | **2** (50%) |

## Never-used attributes by group

| Group | Never used / Total |
|---|---|
| `profile` | 1 / 3 |
| `orders` | 1 / 1 |
| `(ungrouped)` | 1 / 1 |
| `consent` | 0 / 1 |
| `loyalty` | 0 / 1 |

### `(ungrouped)` — 1 never used

| Column | Display name | Type | Source |
|---|---|---|---|
| `hiking_fan` | Hiking Fan | string | `acme_gold.interest_attrs.hiking_fan` |

### `orders` — 1 never used

| Column | Display name | Type | Source |
|---|---|---|---|
| `last_purchase_at` | Last Purchase Date | timestamp | `acme_gold.order_attrs.last_purchase_at` |

### `profile` — 1 never used

| Column | Display name | Type | Source |
|---|---|---|---|
| `phone` | Phone | string | `acme_gold.profile_attrs.phone` |

## Dormant / activation-only attributes

| Column | Status | Segments | Activations | Last used |
|---|---|---|---|---|
| `first_name` | activation_only | 0 | 1 | — |
| `loyalty_tier` | dormant | 1 | 0 | 2024-02-01 |

## Attributes in use (ranked by segment count)

| Column | Display name | Segments | Updated last 180d | Activations | Last used |
|---|---|---|---|---|---|
| `country` | Country | 1 | 1 | 1 | 2026-09-01 |
| `email_opt_in` | Email Opt In | 1 | 1 | 0 | 2026-09-01 |
| `loyalty_tier` | Loyalty Tier | 1 | 0 | 0 | 2024-02-01 |

## Behaviors

| Behavior | Matrix table | Segments | Last used | Columns never used |
|---|---|---|---|---|
| Order Events | `behavior_order_events` | 1 | 2026-09-01 | 1 / 3 |
| Web Visits | `behavior_pageviews` | 0 | — | 1 / 1 |

### Behavior columns never used in a filter or aggregation

- **Order Events**: `sku`
- **Web Visits**: `url`

## Integrity findings

Rule fields that do not map to a configured attribute:

| Field | Classification | # Segments | Segment IDs (first 5) |
|---|---|---|---|
| `known_profile` | matrix_column | 1 | 1003 |
| `legacy_region` | unknown_or_removed | 1 | 1003 |

- `matrix_column`: exists in the audience `customers` table (usually a master/identity table column) — valid.
- `unknown_or_removed`: not in the parent segment config or the matrix table — the segment likely references a deleted attribute.
- `unverified`: matrix schema was not available.

Segment references pointing at segments that no longer exist:

- segment `9999` referenced by 1003

## Caveats

- Usage is derived from segment rule JSON (and activation column mappings if fetched). Journeys, predictive models, workflows or SQL that read the audience tables directly are not covered.
- `last_used` is the most recent `updatedAt` of a segment referencing the field, not the last time the segment ran.
