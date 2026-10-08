# Segment rule JSON — what the analyzer looks for

Returned by `GET /audiences/{parentId}/segments` (list, includes `rule`) and
`GET /audiences/{parentId}/segments/{segmentId}`.

## Top level

```json
{
  "id": "1001", "name": "...", "audienceId": "123",
  "isVisible": true, "numSyndications": 1, "population": 50000,
  "createdAt": "...", "updatedAt": "...",
  "rule": { "type": "And", "conditions": [ ... ] }
}
```

`rule.type` is `And`, `Or` or `Composite`; groups nest arbitrarily via `conditions`.

## Condition types

### 1. Attribute condition
`leftValue.name` = attribute `matrixColumnName` on the parent segment (or a master table column).
```json
{ "type": "Value",
  "leftValue": { "name": "country" },
  "operator": { "type": "Equal", "rightValue": "US", "not": false },
  "exclude": false }
```
Common operators: `Equal`, `In`, `IsNull` (with `not: true` = is not null), `GreaterEqual`,
`TimeWithinPast` (`unit`, `value`), `Contain`, `Regexp`.

### 2. Behavior condition
`leftValue.source.name` = behavior `matrixTableName` (e.g. `behavior_order_events`).
Columns used are in `filter` (`type: Column`) and optionally `aggregation.column`.
```json
{ "type": "Value",
  "leftValue": {
    "source": { "name": "behavior_order_events" },
    "aggregation": { "type": "Count" },
    "filter": { "type": "And", "conditions": [
      { "type": "Column", "column": "timestamp",
        "operator": { "type": "TimeWithinPast", "unit": "day", "value": 30 } },
      { "type": "Column", "column": "category",
        "operator": { "type": "In", "rightValues": ["A", "B"] } } ] } },
  "operator": { "type": "GreaterEqual", "rightValue": 1 } }
```

### 3. Segment reference
Re-uses another segment (often a hidden "building block" segment, `isVisible: false`).
```json
{ "type": "Reference", "id": "1002", "exclude": false }
```
The analyzer credits usage to the segment that holds the actual conditions, so attributes
used only via references are still counted.

## Parent segment config (`GET /audiences/{id}`)

- `attributes[]`: `name` (display), `matrixColumnName` (what rules use), `type`,
  `groupingName`, `parentDatabaseName`, `parentTableName`, `parentColumn`.
- `behaviors[]`: `name`, `matrixTableName` (what rules use), `parentTableName`,
  `schema[]` of `{name, matrixColumnName, parentColumn, type}`.
- `master`: `{parentDatabaseName, parentTableName}` — master table columns are queryable
  in rules but are not listed in `attributes`.

## Activations (`GET /audiences/{id}/segments/{sid}/syndications`)

- `allColumns: true` exports every column.
- `columns[]`: `{ "column": "<output name>", "source": { "column": "<matrix column>" } }`.

## Matrix table schema

`GET https://api.<region>.treasuredata.com/v3/table/show/cdp_audience_{id}/customers` →
`schema` (JSON string of `[name, type, alias]`). Used to tell master-table columns apart
from removed attributes.
