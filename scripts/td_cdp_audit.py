#!/usr/bin/env python3
"""
td_cdp_audit.py — Treasure Data CDP attribute usage audit.

Finds which parent-segment (audience) attributes, behaviors and behavior
columns are actually used by child segments (and, optionally, activations),
and which have never been used.

Supports both full baseline audits and fast incremental runs (fetching and
analyzing only new/modified segments and activations since previous runs).

Pure Python 3.8+ standard library. No third-party dependencies.

Sub-commands
------------
  list-parents   List parent segments visible to the API key.
  fetch          Download parent segment config + all child segments (+ activations).
  analyze        Analyse previously fetched JSON (offline, no API calls).
  run            fetch + analyze in one step.

Authentication (first match wins; the key is never printed or written to disk)
  1. --api-key-env NAME   (environment variable name, default TD_API_KEY)
  2. ~/.td/td.conf        (--td-conf PATH, --profile NAME; same file the `td` CLI uses)

Endpoint (first match wins)
  1. --endpoint https://api-cdp.<region>.treasuredata.com
  2. --region us01|eu01|ap01|ap02
  3. endpoint in td.conf profile (api.* is rewritten to api-cdp.*)
  4. us01
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import configparser
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

__version__ = "1.1.0"

REGIONS = {
    "us01": ("https://api.treasuredata.com", "https://api-cdp.treasuredata.com"),
    "eu01": ("https://api.eu01.treasuredata.com", "https://api-cdp.eu01.treasuredata.com"),
    "ap01": ("https://api.treasuredata.co.jp", "https://api-cdp.treasuredata.co.jp"),
    "ap02": ("https://api.ap02.treasuredata.com", "https://api-cdp.ap02.treasuredata.com"),
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Auth / endpoint resolution
# --------------------------------------------------------------------------- #
def _cdp_from_api(url: str) -> str:
    """https://api.eu01.treasuredata.com -> https://api-cdp.eu01.treasuredata.com"""
    if not url.startswith("http"):
        url = "https://" + url
    p = urllib.parse.urlparse(url)
    host = p.netloc
    if host.startswith("api-cdp."):
        return f"{p.scheme}://{host}"
    if host.startswith("api."):
        host = "api-cdp." + host[len("api."):]
    return f"{p.scheme}://{host}"


def _api_from_cdp(url: str) -> str:
    p = urllib.parse.urlparse(url)
    host = p.netloc.replace("api-cdp.", "api.", 1)
    return f"{p.scheme}://{host}"


def resolve_auth(args) -> Tuple[str, str, str]:
    """Return (api_key, cdp_endpoint, td_api_endpoint). Never logs the key."""
    key = os.environ.get(args.api_key_env, "").strip()
    conf_endpoint = None
    source = f"env:{args.api_key_env}"
    if not key:
        path = os.path.expanduser(args.td_conf)
        cp = configparser.ConfigParser()
        if os.path.exists(path):
            cp.read(path)
        if args.profile not in cp:
            sys.exit(
                f"ERROR: no API key. Set ${args.api_key_env} or add a [{args.profile}] "
                f"section with 'apikey' to {path} (use --profile to pick another section)."
            )
        key = cp[args.profile].get("apikey", "").strip()
        conf_endpoint = cp[args.profile].get("endpoint")
        source = f"{path} [{args.profile}]"
    if not key:
        sys.exit("ERROR: API key is empty.")

    if args.endpoint:
        cdp = _cdp_from_api(args.endpoint)
    elif args.region:
        cdp = REGIONS[args.region][1]
    elif os.environ.get("TD_REGION") in REGIONS:
        cdp = REGIONS[os.environ["TD_REGION"]][1]
    elif conf_endpoint:
        cdp = _cdp_from_api(conf_endpoint)
    else:
        cdp = REGIONS["us01"][1]
    log(f"auth: {source} | endpoint: {cdp}")
    return key, cdp, _api_from_cdp(cdp)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Client:
    def __init__(self, key: str, cdp: str, api: str, timeout: int = 300, retries: int = 4):
        self._key = key
        self.cdp = cdp.rstrip("/")
        self.api = api.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def _get(self, base: str, path: str) -> Any:
        url = base + path
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"TD1 {self._key}",
                    "Accept": "application/json",
                    "User-Agent": f"td-cdp-attribute-usage-audit/{__version__}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                body = e.read()[:300].decode("utf-8", "replace")
                if e.code in (401, 403):
                    sys.exit(f"ERROR {e.code} on {path}: check API key permissions. {body}")
                if e.code == 404:
                    raise LookupError(f"404 on {path}: {body}") from None
                last = RuntimeError(f"HTTP {e.code} on {path}: {body}")
                if e.code not in (429, 500, 502, 503, 504):
                    raise last
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
            wait = 2 ** attempt
            log(f"  retry {attempt + 1}/{self.retries} in {wait}s ({last})")
            time.sleep(wait)
        raise RuntimeError(f"GET {path} failed: {last}")

    def cdp_get(self, path: str) -> Any:
        return self._get(self.cdp, path)

    def api_get(self, path: str) -> Any:
        return self._get(self.api, path)


def _as_list(resp: Any, *keys: str) -> List[dict]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in keys + ("data", "items", "results"):
            if isinstance(resp.get(k), list):
                return resp[k]
    return []


# --------------------------------------------------------------------------- #
# Fetch (Incremental aware)
# --------------------------------------------------------------------------- #
def cmd_list_parents(args) -> None:
    key, cdp, api = resolve_auth(args)
    c = Client(key, cdp, api, timeout=args.timeout)
    rows = _as_list(c.cdp_get("/audiences"), "audiences")
    rows.sort(key=lambda r: str(r.get("name", "")))
    print(f"{'ID':>10}  {'POPULATION':>13}  NAME")
    for r in rows:
        pop = r.get("population")
        print(f"{r.get('id', ''):>10}  {pop if pop is not None else '':>13}  {r.get('name', '')}")
    log(f"{len(rows)} parent segment(s)")


def fetch(args, client: Client) -> str:
    pid = str(args.parent_segment_id)
    out = os.path.abspath(args.out or f"td_cdp_audit_{pid}")
    os.makedirs(out, exist_ok=True)

    prev_meta = _load(out, "fetch_meta.json")
    is_incremental = bool(prev_meta and not getattr(args, "full_refresh", False) and os.path.exists(os.path.join(out, "segments.json")))
    prev_fetched_at = prev_meta.get("fetched_at") if is_incremental else None

    if is_incremental:
        log(f"[mode] incremental fetch enabled (baseline cached from {prev_fetched_at})")
    else:
        log("[mode] full baseline fetch")

    log(f"[1/4] parent segment {pid} config")
    audience = client.cdp_get(f"/audiences/{pid}")
    _dump(out, "audience.json", audience)
    log(f"      {len(audience.get('attributes', []))} attributes, "
        f"{len(audience.get('behaviors', []))} behaviors")

    log("[2/4] matrix (customers) table schema — used to classify unmapped rule fields")
    schema: List[str] = []
    try:
        t = client.api_get(f"/v3/table/show/cdp_audience_{pid}/customers")
        schema = [c[0] for c in json.loads(t.get("schema", "[]"))]
    except Exception as e:
        log(f"      skipped ({e})")
    _dump(out, "customers_schema.json", schema)

    log("[3/4] child segments")
    fresh_segs = _as_list(client.cdp_get(f"/audiences/{pid}/segments"), "segments")

    cached_segs_map: Dict[str, dict] = {}
    cached_acts: List[dict] = []
    if is_incremental:
        for s in _load(out, "segments.json", []):
            cached_segs_map[str(s["id"])] = s
        cached_acts = _load(out, "activations.json", []) or []

    new_segs = []
    updated_segs = []
    unchanged_segs = []
    current_ids = {str(s["id"]) for s in fresh_segs}
    deleted_ids = set(cached_segs_map.keys()) - current_ids if is_incremental else set()

    for s in fresh_segs:
        sid = str(s["id"])
        cached = cached_segs_map.get(sid)
        if not cached:
            new_segs.append(s)
        elif (cached.get("updatedAt") != s.get("updatedAt") or
              cached.get("numSyndications") != s.get("numSyndications")):
            updated_segs.append(s)
        else:
            unchanged_segs.append(s)
            if "rule" not in s and "rule" in cached:
                s["rule"] = cached["rule"]

    # Fetch missing rules for new or updated segments
    missing = [s for s in (new_segs + updated_segs) if "rule" not in s]
    if missing:
        log(f"      fetching rules for {len(missing)} new/updated segment(s)")

        def one(s):
            try:
                return client.cdp_get(f"/audiences/{pid}/segments/{s['id']}")
            except Exception as e:
                return {**s, "_error": str(e)}

        full = _pmap(one, missing, args.workers)
        by_id = {str(s["id"]): s for s in full}
        fresh_segs = [by_id.get(str(s["id"]), s) for s in fresh_segs]

    _dump(out, "segments.json", fresh_segs)
    if is_incremental:
        log(f"      {len(fresh_segs)} total segments ({len(new_segs)} new, {len(updated_segs)} modified, "
            f"{len(deleted_ids)} deleted, {len(unchanged_segs)} unchanged)")
    else:
        log(f"      {len(fresh_segs)} segments")

    # Activations
    acts: List[dict] = []
    if args.include_activations:
        def acts_for(s):
            try:
                r = _as_list(client.cdp_get(f"/audiences/{pid}/segments/{s['id']}/syndications"))
                return [{**a, "segmentId": str(s["id"])} for a in r]
            except Exception as e:
                return [{"segmentId": str(s["id"]), "_error": str(e)}]

        if is_incremental and cached_acts:
            cached_acts_by_seg = collections.defaultdict(list)
            for a in cached_acts:
                cached_acts_by_seg[str(a.get("segmentId"))].append(a)

            # Targets needing refresh
            targets = [s for s in (new_segs + updated_segs) if (s.get("numSyndications") or 0) > 0]
            # Also catch any unchanged segment that was missing from cached activations
            for s in unchanged_segs:
                sid = str(s["id"])
                if (s.get("numSyndications") or 0) > 0 and sid not in cached_acts_by_seg:
                    targets.append(s)

            target_ids = {str(s["id"]) for s in targets}
            # Keep unchanged cached activations
            for sid, a_list in cached_acts_by_seg.items():
                if sid in current_ids and sid not in target_ids:
                    acts.extend(a_list)

            if targets:
                log(f"[4/4] activations: fetching {len(targets)} new/updated segments (reusing {len(acts)} cached)")
                for lst in _pmap(acts_for, targets, args.workers):
                    acts.extend(lst)
            else:
                log(f"[4/4] activations: all {len(acts)} cached activations up-to-date (0 API calls needed)")
        else:
            targets = [s for s in fresh_segs if (s.get("numSyndications") or 0) > 0]
            log(f"[4/4] activations for {len(targets)} segment(s) with activations")
            for lst in _pmap(acts_for, targets, args.workers):
                acts.extend(lst)
    else:
        log("[4/4] activations skipped (use --include-activations)")

    _dump(out, "activations.json", acts)

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _dump(out, "fetch_meta.json", {
        "parent_segment_id": pid,
        "fetched_at": now_iso,
        "previous_fetched_at": prev_fetched_at,
        "endpoint": client.cdp,
        "include_activations": bool(args.include_activations),
        "tool_version": __version__,
        "delta": {
            "is_incremental": is_incremental,
            "new_segments": len(new_segs) if is_incremental else len(fresh_segs),
            "modified_segments": len(updated_segs) if is_incremental else 0,
            "deleted_segments": len(deleted_ids) if is_incremental else 0,
            "unchanged_segments": len(unchanged_segs) if is_incremental else 0,
        }
    })
    log(f"raw data saved to {out}")
    return out


def _pmap(fn, items, workers):
    done = 0
    results = []
    with cf.ThreadPoolExecutor(max(1, workers)) as ex:
        for r in ex.map(fn, items):
            results.append(r)
            done += 1
            if done % 100 == 0 or done == len(items):
                log(f"      {done}/{len(items)}")
    return results


def _dump(out: str, name: str, obj: Any) -> None:
    with open(os.path.join(out, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def _load(d: str, name: str, default: Any = None) -> Any:
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# Analyse (pure functions — unit tested)
# --------------------------------------------------------------------------- #
class Usage:
    def __init__(self) -> None:
        self.attr: Dict[str, Set[str]] = collections.defaultdict(set)
        self.beh: Dict[str, Set[str]] = collections.defaultdict(set)
        self.beh_col: Dict[Tuple[str, str], Set[str]] = collections.defaultdict(set)
        self.refs: Dict[str, Set[str]] = collections.defaultdict(set)
        self.act_attr: Dict[str, Set[str]] = collections.defaultdict(set)
        self.act_all_columns: Set[str] = set()


def _filter_columns(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        if node.get("type") == "Column" and isinstance(node.get("column"), str):
            yield node["column"]
        for v in node.values():
            yield from _filter_columns(v)
    elif isinstance(node, list):
        for v in node:
            yield from _filter_columns(v)


def walk_rule(node: Any, sid: str, u: Usage) -> None:
    """Recursively collect attribute / behavior / reference usage from a segment rule."""
    if isinstance(node, dict):
        if node.get("type") == "Reference" and node.get("id") is not None:
            u.refs[str(node["id"])].add(sid)
        lv = node.get("leftValue")
        if isinstance(lv, dict):
            src = lv.get("source")
            if isinstance(src, dict) and src.get("name"):
                b = src["name"]
                u.beh[b].add(sid)
                for col in _filter_columns(lv.get("filter")):
                    u.beh_col[(b, col)].add(sid)
                agg = lv.get("aggregation") or {}
                if isinstance(agg, dict) and isinstance(agg.get("column"), str):
                    u.beh_col[(b, agg["column"])].add(sid)
            elif isinstance(lv.get("name"), str):
                u.attr[lv["name"]].add(sid)
        for v in node.values():
            walk_rule(v, sid, u)
    elif isinstance(node, list):
        for v in node:
            walk_rule(v, sid, u)


def collect_activation_usage(acts: List[dict], u: Usage) -> None:
    for a in acts:
        if a.get("_error"):
            continue
        sid = str(a.get("segmentId"))
        if a.get("allColumns"):
            u.act_all_columns.add(sid)
        for c in a.get("columns") or []:
            src = c.get("source") if isinstance(c.get("source"), dict) else {}
            col = src.get("column") or c.get("column")
            if isinstance(col, str):
                u.act_attr[col].add(sid)


def _behavior_keys(b: dict) -> List[str]:
    keys = [b.get("matrixTableName"), b.get("name")]
    if b.get("parentTableName"):
        keys += [f"behavior_{b['parentTableName']}", b["parentTableName"]]
    return [k for k in keys if k]


def compute_delta(segments: List[dict], audience: dict, since: str) -> dict:
    """Analyze segments created or modified since a given ISO date/timestamp."""
    since_norm = since[:19].replace(" ", "T")
    delta_created = []
    delta_modified = []
    prior_segs = []

    for s in segments:
        c_at = str(s.get("createdAt") or "")[:19].replace(" ", "T")
        u_at = str(s.get("updatedAt") or "")[:19].replace(" ", "T")
        if c_at and c_at < since_norm:
            prior_segs.append(s)

        if c_at and c_at >= since_norm:
            delta_created.append(s)
        elif u_at and u_at >= since_norm:
            delta_modified.append(s)

    delta_segs = delta_created + delta_modified
    u_delta = Usage()
    for s in delta_segs:
        if s.get("rule"):
            walk_rule(s["rule"], str(s["id"]), u_delta)

    u_prior = Usage()
    for s in prior_segs:
        if s.get("rule"):
            walk_rule(s["rule"], str(s["id"]), u_prior)

    attrs = audience.get("attributes", []) or []
    attr_map = {a.get("matrixColumnName") or a.get("name"): a for a in attrs}

    # Newly adopted: used in delta segments but never used in prior segments
    newly_adopted_attrs = []
    for col in sorted(u_delta.attr.keys()):
        if col not in u_prior.attr:
            meta = attr_map.get(col, {})
            newly_adopted_attrs.append({
                "column": col,
                "name": meta.get("name", col),
                "group": meta.get("groupingName", ""),
                "segments_count": len(u_delta.attr[col]),
            })

    newly_adopted_behs = [b for b in sorted(u_delta.beh.keys()) if b not in u_prior.beh]

    return {
        "since": since,
        "segments_created_count": len(delta_created),
        "segments_modified_count": len(delta_modified),
        "total_delta_segments": len(delta_segs),
        "attributes_used_in_delta_count": len(u_delta.attr),
        "newly_adopted_attributes": newly_adopted_attrs,
        "newly_adopted_behaviors": newly_adopted_behs,
        "sample_new_segments": [{"id": s.get("id"), "name": s.get("name"), "createdAt": s.get("createdAt")}
                                for s in delta_created[:5]],
    }


def analyze(audience: dict, segments: List[dict], activations: List[dict],
            customers_schema: List[str], recent_days: int,
            today: Optional[dt.date] = None, since: Optional[str] = None) -> dict:
    today = today or dt.date.today()
    cutoff = (today - dt.timedelta(days=recent_days)).isoformat()
    seg_by_id = {str(s["id"]): s for s in segments}
    u = Usage()
    for s in segments:
        if s.get("rule"):
            walk_rule(s["rule"], str(s["id"]), u)
    collect_activation_usage(activations or [], u)

    def stats(ids: Set[str]) -> Tuple[int, int, str]:
        dates = [str(seg_by_id[i].get("updatedAt") or "")[:10] for i in ids if i in seg_by_id]
        recent = sum(1 for d in dates if d and d >= cutoff)
        return len(ids), recent, max(dates) if dates else ""

    rows: List[dict] = []
    attrs = audience.get("attributes", []) or []
    attr_cols = set()
    for a in attrs:
        col = a.get("matrixColumnName") or a.get("name")
        attr_cols.add(col)
        n, rec, last = stats(u.attr.get(col, set()))
        acts = len(u.act_attr.get(col, set()))
        rows.append({
            "kind": "attribute", "group": a.get("groupingName") or "", "name": (a.get("name") or "").strip(),
            "column": col, "type": a.get("type") or "",
            "source": ".".join(str(x) for x in (a.get("parentDatabaseName"), a.get("parentTableName"),
                                                 a.get("parentColumn")) if x),
            "segments": n, "segments_recent": rec, "last_used": last, "activations": acts,
            "status": _status(n, rec, acts),
        })

    for b in audience.get("behaviors", []) or []:
        key = next((k for k in _behavior_keys(b) if k in u.beh), b.get("matrixTableName") or b.get("name"))
        n, rec, last = stats(u.beh.get(key, set()))
        rows.append({
            "kind": "behavior", "group": "", "name": b.get("name") or "", "column": key, "type": "behavior",
            "source": ".".join(str(x) for x in (b.get("parentDatabaseName"), b.get("parentTableName")) if x),
            "segments": n, "segments_recent": rec, "last_used": last, "activations": 0,
            "status": _status(n, rec, 0),
        })
        for c in b.get("schema", []) or []:
            cn = c.get("matrixColumnName") or c.get("name")
            n2, rec2, last2 = stats(u.beh_col.get((key, cn), set()))
            rows.append({
                "kind": "behavior_column", "group": b.get("name") or "", "name": c.get("name") or "",
                "column": cn, "type": c.get("type") or "",
                "source": f"{b.get('parentTableName', '')}.{c.get('parentColumn', cn)}",
                "segments": n2, "segments_recent": rec2, "last_used": last2, "activations": 0,
                "status": _status(n2, rec2, 0),
            })

    # Rule fields that don't map to a configured attribute
    schema = set(customers_schema or [])
    unmapped = []
    for name, ids in sorted(u.attr.items()):
        if name in attr_cols:
            continue
        unmapped.append({
            "field": name,
            "classification": "matrix_column" if name in schema else
            ("unknown_or_removed" if schema else "unverified"),
            "segments": sorted(ids, key=int if all(i.isdigit() for i in ids) else str),
        })

    known_beh = {k for b in audience.get("behaviors", []) or [] for k in _behavior_keys(b)}
    unknown_beh = [{"behavior": b, "segments": sorted(ids)} for b, ids in u.beh.items() if b not in known_beh]
    broken_refs = [{"referenced_segment": r, "referenced_by": sorted(ids)}
                   for r, ids in u.refs.items() if r not in seg_by_id]

    summary = {
        "parent_segment_id": str(audience.get("id", "")),
        "parent_segment_name": audience.get("name", ""),
        "population": audience.get("population"),
        "segments_total": len(segments),
        "segments_with_rules": sum(1 for s in segments if s.get("rule")),
        "segments_fetch_errors": sum(1 for s in segments if s.get("_error")),
        "activations_analyzed": len([a for a in activations or [] if not a.get("_error")]),
        "activations_all_columns": len(u.act_all_columns),
        "recent_days": recent_days,
        "counts": {},
    }
    for kind in ("attribute", "behavior", "behavior_column"):
        k = [r for r in rows if r["kind"] == kind]
        summary["counts"][kind] = {
            "total": len(k),
            **collections.Counter(r["status"] for r in k),
        }

    delta_result = None
    if since:
        delta_result = compute_delta(segments, audience, since)

    return {
        "summary": summary,
        "rows": rows,
        "unmapped_rule_fields": unmapped,
        "unknown_behaviors": unknown_beh,
        "broken_references": broken_refs,
        "delta": delta_result,
    }


def _status(n: int, recent: int, acts: int) -> str:
    if n == 0 and acts == 0:
        return "never_used"
    if n == 0:
        return "activation_only"
    if recent == 0:
        return "dormant"
    return "active"


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def write_outputs(res: dict, out: str) -> None:
    rows = res["rows"]
    with open(os.path.join(out, "attribute_usage.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["kind"])
        w.writeheader()
        w.writerows(rows)
    _dump(out, "summary.json", {k: v for k, v in res.items() if k != "rows"})
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(res))


def render_markdown(res: dict) -> str:
    s = res["summary"]
    rows = res["rows"]
    delta = res.get("delta")
    L: List[str] = []
    p = L.append
    p(f"# CDP Attribute Usage Audit — Parent Segment {s['parent_segment_id']} ({s['parent_segment_name']})\n")
    p(f"Generated {dt.date.today().isoformat()} by td-cdp-attribute-usage-audit v{__version__}. "
      f"Analyzed **{s['segments_total']} segments** ({s['segments_with_rules']} with rules"
      + (f", {s['segments_fetch_errors']} fetch errors" if s["segments_fetch_errors"] else "")
      + f") and **{s['activations_analyzed']} activations**.\n")

    if delta:
        p(f"## Incremental Activity (Since {delta['since']})\n")
        p("| Metric | Count |")
        p("|---|---|")
        p(f"| New segments created | {delta['segments_created_count']} |")
        p(f"| Existing segments modified | {delta['segments_modified_count']} |")
        p(f"| Attributes referenced in this window | {delta['attributes_used_in_delta_count']} |")
        p(f"| **Newly adopted attributes (first-time use)** | **{len(delta['newly_adopted_attributes'])}** |")
        p("")
        if delta["newly_adopted_attributes"]:
            p("### Newly Adopted Attributes\n")
            p("| Column | Display Name | Group | Segments Count |")
            p("|---|---|---|---|")
            for a in delta["newly_adopted_attributes"]:
                p(f"| `{a['column']}` | {_md(a['name'])} | `{a['group']}` | {a['segments_count']} |")
            p("")
        if delta["newly_adopted_behaviors"]:
            p("### Newly Adopted Behaviors\n")
            for b in delta["newly_adopted_behaviors"]:
                p(f"- `{b}`")
            p("")

    p("Status legend: `never_used` (no segment rule or activation) · `activation_only` "
      "(exported but never filtered on) · `dormant` (used only by segments not updated in the last "
      f"{s['recent_days']} days) · `active`.\n")
    p("## Summary\n")
    p("| Object | Total | Active | Dormant | Activation only | Never used |\n|---|---|---|---|---|---|")
    labels = {"attribute": "Attributes", "behavior": "Behaviors", "behavior_column": "Behavior columns"}
    for k, lbl in labels.items():
        c = s["counts"].get(k, {})
        tot = c.get("total", 0)
        nu = c.get("never_used", 0)
        pct = f" ({nu * 100 // tot}%)" if tot else ""
        p(f"| {lbl} | {tot} | {c.get('active', 0)} | {c.get('dormant', 0)} | "
          f"{c.get('activation_only', 0)} | **{nu}**{pct} |")
    p("")
    if s["activations_analyzed"] == 0:
        p("> **Note:** activations were not analyzed. An attribute marked `never_used` may still be "
          "exported by an activation. Re-run with `--include-activations` before removing anything.\n")
    if s["activations_all_columns"]:
        p(f"> **Note:** {s['activations_all_columns']} segment(s) have an activation exporting *all columns*; "
          "every attribute is implicitly exported by those.\n")

    attrs = [r for r in rows if r["kind"] == "attribute"]
    groups: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in attrs:
        groups[r["group"] or "(ungrouped)"].append(r)
    p("## Never-used attributes by group\n")
    p("| Group | Never used / Total |\n|---|---|")
    for g, v in sorted(groups.items(), key=lambda kv: -sum(r["status"] == "never_used" for r in kv[1])):
        p(f"| `{g}` | {sum(r['status'] == 'never_used' for r in v)} / {len(v)} |")
    p("")
    for g, v in sorted(groups.items()):
        nu = [r for r in v if r["status"] == "never_used"]
        if not nu:
            continue
        p(f"### `{g}` — {len(nu)} never used\n")
        p("| Column | Display name | Type | Source |\n|---|---|---|---|")
        for r in nu:
            p(f"| `{r['column']}` | {_md(r['name'])} | {r['type']} | `{r['source']}` |")
        p("")

    other = [r for r in attrs if r["status"] in ("dormant", "activation_only")]
    if other:
        p("## Dormant / activation-only attributes\n")
        p("| Column | Status | Segments | Activations | Last used |\n|---|---|---|---|---|")
        for r in sorted(other, key=lambda r: r["last_used"]):
            p(f"| `{r['column']}` | {r['status']} | {r['segments']} | {r['activations']} | {r['last_used'] or '—'} |")
        p("")

    p("## Attributes in use (ranked by segment count)\n")
    p(f"| Column | Display name | Segments | Updated last {s['recent_days']}d | Activations | Last used |\n"
      "|---|---|---|---|---|---|")
    for r in sorted([r for r in attrs if r["segments"]], key=lambda r: -r["segments"]):
        p(f"| `{r['column']}` | {_md(r['name'])} | {r['segments']} | {r['segments_recent']} | "
          f"{r['activations']} | {r['last_used']} |")
    p("")

    p("## Behaviors\n")
    p("| Behavior | Matrix table | Segments | Last used | Columns never used |\n|---|---|---|---|---|")
    bcols = [r for r in rows if r["kind"] == "behavior_column"]
    for r in sorted([r for r in rows if r["kind"] == "behavior"], key=lambda r: -r["segments"]):
        cols = [c for c in bcols if c["group"] == r["name"]]
        nu = sum(c["status"] == "never_used" for c in cols)
        p(f"| {_md(r['name'])} | `{r['column']}` | {r['segments']} | {r['last_used'] or '—'} | {nu} / {len(cols)} |")
    p("")
    by_b: Dict[str, List[str]] = collections.defaultdict(list)
    for c in bcols:
        if c["status"] == "never_used":
            by_b[c["group"]].append(c["column"])
    if by_b:
        p("### Behavior columns never used in a filter or aggregation\n")
        for b, cols in by_b.items():
            p(f"- **{_md(b)}**: " + ", ".join(f"`{c}`" for c in cols))
        p("")

    issues = res["unmapped_rule_fields"] or res["unknown_behaviors"] or res["broken_references"]
    if issues:
        p("## Integrity findings\n")
        if res["unmapped_rule_fields"]:
            p("Rule fields that do not map to a configured attribute:\n")
            p("| Field | Classification | # Segments | Segment IDs (first 5) |\n|---|---|---|---|")
            for x in res["unmapped_rule_fields"]:
                p(f"| `{x['field']}` | {x['classification']} | {len(x['segments'])} | "
                  f"{', '.join(x['segments'][:5])} |")
            p("\n- `matrix_column`: exists in the audience `customers` table (usually a master/identity "
              "table column) — valid.\n- `unknown_or_removed`: not in the parent segment config or the "
              "matrix table — the segment likely references a deleted attribute.\n- `unverified`: matrix "
              "schema was not available.\n")
        if res["unknown_behaviors"]:
            p("Behaviors referenced in rules but not configured on the parent segment:\n")
            for x in res["unknown_behaviors"]:
                p(f"- `{x['behavior']}` — segments {', '.join(x['segments'][:5])}")
            p("")
        if res["broken_references"]:
            p("Segment references pointing at segments that no longer exist:\n")
            for x in res["broken_references"]:
                p(f"- segment `{x['referenced_segment']}` referenced by {', '.join(x['referenced_by'][:5])}")
            p("")
    p("## Caveats\n")
    p("- Usage is derived from segment rule JSON (and activation column mappings if fetched). "
      "Journeys, predictive models, workflows or SQL that read the audience tables directly are not "
      "covered.\n- `last_used` is the most recent `updatedAt` of a segment referencing the field, not "
      "the last time the segment ran.\n")
    return "\n".join(L)


def _md(s: str) -> str:
    return str(s).replace("|", "\\|").strip()


def run_analyze(in_dir: str, out_dir: Optional[str], recent_days: int,
                since: Optional[str] = None, since_last_run: bool = False) -> dict:
    audience = _load(in_dir, "audience.json")
    segments = _load(in_dir, "segments.json")
    if audience is None or segments is None:
        sys.exit(f"ERROR: {in_dir} must contain audience.json and segments.json (run `fetch` first).")

    meta = _load(in_dir, "fetch_meta.json", {})
    if since_last_run and not since:
        since = meta.get("previous_fetched_at")
        if not since:
            log("warning: --since-last-run requested, but no previous_fetched_at found in fetch_meta.json")

    res = analyze(audience, segments, _load(in_dir, "activations.json", []),
                  _load(in_dir, "customers_schema.json", []), recent_days, since=since)
    out = out_dir or in_dir
    os.makedirs(out, exist_ok=True)
    write_outputs(res, out)
    c = res["summary"]["counts"]
    log(f"attributes never used: {c['attribute'].get('never_used', 0)}/{c['attribute']['total']} | "
        f"behaviors never used: {c['behavior'].get('never_used', 0)}/{c['behavior']['total']} | "
        f"behavior columns never used: {c['behavior_column'].get('never_used', 0)}/{c['behavior_column']['total']}")
    if res.get("delta"):
        d = res["delta"]
        log(f"incremental delta (since {d['since']}): {d['segments_created_count']} created, "
            f"{d['segments_modified_count']} modified, {len(d['newly_adopted_attributes'])} newly adopted attributes")
    log(f"wrote {os.path.join(out, 'report.md')}, attribute_usage.csv, summary.json")
    return res


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="td_cdp_audit.py",
        description="Audit which Treasure Data CDP parent-segment attributes are used by segments.")
    ap.add_argument("--version", action="version", version=__version__)

    auth = argparse.ArgumentParser(add_help=False)
    auth.add_argument("--api-key-env", default="TD_API_KEY", help="env var holding the API key (default TD_API_KEY)")
    auth.add_argument("--td-conf", default="~/.td/td.conf", help="td CLI config file fallback")
    auth.add_argument("--profile", default="account", help="section in td.conf (default: account)")
    auth.add_argument("--region", choices=sorted(REGIONS), help="TD region (default: from td.conf or us01)")
    auth.add_argument("--endpoint", help="explicit API or CDP endpoint URL")
    auth.add_argument("--timeout", type=int, default=300, help="HTTP timeout seconds (default 300)")

    fetch_opts = argparse.ArgumentParser(add_help=False)
    fetch_opts.add_argument("-p", "--parent-segment-id", required=True, type=int)
    fetch_opts.add_argument("-o", "--out", help="output directory (default ./td_cdp_audit_<id>)")
    fetch_opts.add_argument("--include-activations", action="store_true",
                            help="also fetch activation column mappings (one call per activated segment)")
    fetch_opts.add_argument("--workers", type=int, default=8, help="parallel requests (default 8)")
    fetch_opts.add_argument("--full-refresh", action="store_true",
                            help="force full fresh download, ignoring cached segments and activations")

    an_opts = argparse.ArgumentParser(add_help=False)
    an_opts.add_argument("--recent-days", type=int, default=180,
                         help="window for 'dormant' classification (default 180)")
    an_opts.add_argument("--since", help="ISO date/timestamp (YYYY-MM-DD); compute delta of activity since this time")
    an_opts.add_argument("--since-last-run", action="store_true",
                         help="automatically set --since to the previous run's fetched_at timestamp")

    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list-parents", parents=[auth], help="list parent segments")
    sp.add_parser("fetch", parents=[auth, fetch_opts], help="download raw JSON (incremental by default if cache exists)")
    a = sp.add_parser("analyze", parents=[an_opts], help="analyze downloaded JSON (offline)")
    a.add_argument("-i", "--in", dest="in_dir", required=True, help="directory produced by fetch")
    a.add_argument("-o", "--out", help="report output directory (default: same as --in)")
    sp.add_parser("run", parents=[auth, fetch_opts, an_opts], help="fetch + analyze")
    return ap


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if args.cmd == "list-parents":
        cmd_list_parents(args)
    elif args.cmd == "analyze":
        run_analyze(args.in_dir, args.out, args.recent_days,
                    since=getattr(args, "since", None),
                    since_last_run=getattr(args, "since_last_run", False))
    else:
        key, cdp, api = resolve_auth(args)
        out = fetch(args, Client(key, cdp, api, timeout=args.timeout))
        if args.cmd == "run":
            run_analyze(out, out, args.recent_days,
                        since=getattr(args, "since", None),
                        since_last_run=getattr(args, "since_last_run", False))


if __name__ == "__main__":
    main()
