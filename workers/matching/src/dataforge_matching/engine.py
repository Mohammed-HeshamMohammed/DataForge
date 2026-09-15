"""Deterministic entity resolution.

Contract (schema_version 1):
    request = {schema_version, entity_type, mapping, rows: [{id, row_number, raw}], settings, constraints}
    result  = {schema_version, policy_version, decisions, clusters, canonical, metrics}

Evidence explanations never contain row values, so decisions are safe to log.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from itertools import combinations
from typing import Callable, Iterable

from rapidfuzz import fuzz

from . import POLICY_VERSION, SCHEMA_VERSION
from .normalize import ALL_ROLES, NORMALIZATION_VERSION, POSITIONAL_ROLES, normalize_row

WEIGHTS = {
    "identifier": 0.30, "phone": 0.32, "email": 0.28, "address": 0.20, "mailing_address": 0.10,
    "name": 0.15, "region": 0.05, "url": 0.20,
}
STRICTNESS = {"conservative": (0.95, 0.75), "balanced": (0.90, 0.70)}
DEFAULT_SETTINGS = {"strictness": "conservative", "max_block_size": 200, "default_region": "US"}


class MatchRequestError(ValueError):
    """Invalid mapping, settings, or schema version. Fails the job before candidate generation."""


def validate_request(request: dict) -> dict:
    if request.get("schema_version") != SCHEMA_VERSION:
        raise MatchRequestError(f"Unsupported match request schema_version {request.get('schema_version')!r}")
    mapping = request.get("mapping") or {}
    unknown = sorted({role for role in mapping.values() if role not in ALL_ROLES})
    if unknown:
        raise MatchRequestError(f"Unknown roles in mapping: {unknown}")
    for role in POSITIONAL_ROLES:
        if list(mapping.values()).count(role) > 1:
            raise MatchRequestError(f"Role {role!r} is positional and may be mapped to only one column")
    evidence_roles = {"identifier", "phone", "email", "address", "mailing_address", "url"}
    if not evidence_roles & set(mapping.values()):
        raise MatchRequestError("At least one identifier, contact, address, or URL field is required")
    settings = {**DEFAULT_SETTINGS, **(request.get("settings") or {})}
    if settings["strictness"] not in STRICTNESS:
        raise MatchRequestError(f"Unknown strictness {settings['strictness']!r}")
    if not 2 <= int(settings["max_block_size"]) <= 5000:
        raise MatchRequestError("max_block_size must be between 2 and 5000")
    return settings


def _block_keys(n: dict) -> Iterable[str]:
    for column, value in n["identifier"].items():
        yield f"id:{column}:{value}"
    for value in n["phone"]:
        if sum(ch.isdigit() for ch in value) >= 10:
            yield f"phone:{value}"
    for value in n["email"]:
        yield f"email:{value}"
    locality = (n.get("postal_code") or "")[:3] or n.get("city", "")
    for role, prefix in (("address", "addr"), ("mailing_address", "maddr")):
        parsed = n.get(role)
        if parsed and parsed["house_number"] and parsed["street"]:
            street_stem = parsed["street"].split()[0][:4]
            yield f"{prefix}:{parsed['house_number']}:{street_stem}:{locality}" if locality else f"{prefix}:{parsed['house_number']}:{parsed['street']}"
    name_tokens = sorted(n.get("name", "").split())
    if len(name_tokens) >= 2 and n.get("postal_code"):
        yield f"name_postal:{' '.join(name_tokens)}:{n['postal_code']}"
    if n.get("url"):
        yield f"url:{n['url']}"


def _block_id(key: str) -> str:
    return key.split(":", 1)[0] + ":" + hashlib.sha1(key.encode()).hexdigest()[:10]


def generate_candidates(normalized: dict[str, dict], max_block_size: int) -> tuple[dict[tuple[str, str], list[str]], dict]:
    blocks: dict[str, list[str]] = defaultdict(list)
    for row_id, n in normalized.items():
        for key in set(_block_keys(n)):
            blocks[key].append(row_id)
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    oversized: list[dict] = []
    for key, members in blocks.items():
        if len(members) < 2:
            continue
        if len(members) > max_block_size:
            # Never compare quadratically; report so the user can add a stronger field.
            oversized.append({"block_id": _block_id(key), "kind": key.split(":", 1)[0], "size": len(members)})
            continue
        for left, right in combinations(sorted(members), 2):
            pairs[(left, right)].append(_block_id(key))
    metrics = {
        "blocks": sum(1 for members in blocks.values() if len(members) >= 2),
        "max_block_size": max((len(m) for m in blocks.values()), default=0),
        "oversized_blocks": sorted(oversized, key=lambda b: -b["size"])[:50],
        "oversized_block_count": len(oversized),
        "candidate_pairs": len(pairs),
    }
    return pairs, metrics


def _ratio(a: str, b: str) -> float:
    return fuzz.token_sort_ratio(a, b) / 100.0


def compare(left: dict, right: dict) -> tuple[list[dict], float, int]:
    """Field-level evidence. Returns (evidence, weighted score over comparable fields, comparable count)."""
    evidence: list[dict] = []
    weighted = total = 0.0

    def add(field: str, similarity: float, result: str, strength: str, explanation: str) -> None:
        nonlocal weighted, total
        evidence.append({"field": field, "similarity": round(similarity, 3), "result": result, "strength": strength, "explanation": explanation})
        weighted += WEIGHTS[field] * similarity
        total += WEIGHTS[field]

    shared_columns = sorted(set(left["identifier"]) & set(right["identifier"]))
    for column in shared_columns:
        if left["identifier"][column] == right["identifier"][column]:
            add("identifier", 1.0, "exact", "strong", f"Same identifier in {column}")
        else:
            add("identifier", 0.0, "conflict", "guard", f"Different identifiers in {column}")

    for field, label in (("phone", "phone"), ("email", "email")):
        if left[field] and right[field]:
            if left[field] & right[field]:
                add(field, 1.0, "exact", "strong", f"Exact {label} match")
            else:
                add(field, 0.0, "different", "none", f"No shared {label}")

    for field, label in (("address", "Address"), ("mailing_address", "Mailing address")):
        a, b = left.get(field), right.get(field)
        if not (a and b):
            continue
        if a["house_number"] and b["house_number"] and a["house_number"] != b["house_number"]:
            add(field, 0.0, "conflict", "guard", f"{label}: different house number")
        elif a["unit"] and b["unit"] and a["unit"] != b["unit"]:
            add(field, 0.0, "conflict", "guard", f"{label}: different unit")
        else:
            similarity = _ratio(a["full"], b["full"])
            strong = bool(a["house_number"]) and a["house_number"] == b["house_number"] and _ratio(a["street"], b["street"]) >= 0.92
            result = "exact" if similarity == 1.0 else "similar" if similarity >= 0.8 else "different"
            detail = "house number and street agree" if strong else f"similarity {similarity:.2f}"
            add(field, similarity, result, "strong" if strong else "supporting", f"{label}: {detail}")

    if left.get("name") and right.get("name"):
        similarity = _ratio(left["name"], right["name"])
        result = "exact" if similarity == 1.0 else "similar" if similarity >= 0.8 else "different"
        add("name", similarity, result, "supporting", f"Name similarity {similarity:.2f}")

    if left.get("postal_code") and right.get("postal_code"):
        same = left["postal_code"] == right["postal_code"]
        add("region", float(same), "exact" if same else "different", "supporting", "Postal code agrees" if same else "Postal code differs")
    elif left.get("city") and right.get("city"):
        same = left["city"] == right["city"]
        add("region", float(same), "exact" if same else "different", "supporting", "City agrees" if same else "City differs")

    if left.get("url") and right.get("url"):
        same = left["url"] == right["url"]
        add("url", float(same), "exact" if same else "different", "strong" if same else "none", "Same canonical URL" if same else "Different URL")

    score = weighted / total if total else 0.0
    return evidence, score, len({item["field"] for item in evidence})


def decide(evidence: list[dict], score: float, comparable: int, strictness: str) -> tuple[str, str]:
    match_threshold, review_threshold = STRICTNESS[strictness]
    guards = [item["explanation"] for item in evidence if item["strength"] == "guard"]
    strong = [item for item in evidence if item["strength"] == "strong"]
    if guards:
        return "non_match", "Cannot auto-merge: " + "; ".join(guards)
    if any(item["field"] == "identifier" for item in strong):
        return "match", "Same identifier with no contradiction"
    if strong and score >= match_threshold and comparable >= 2:
        return "match", f"Strong evidence ({', '.join(i['field'] for i in strong)}) and score {score:.2f}"
    if strong or (score >= review_threshold and comparable >= 2):
        why = "strong evidence but score below auto-match threshold" if strong else "similar values without strong evidence"
        return "possible_match", f"Needs review: {why} (score {score:.2f})"
    if comparable == 0:
        return "non_match", "No comparable fields"
    return "non_match", f"Score {score:.2f} below review threshold"


def cluster(
    row_order: list[str],
    normalized: dict[str, dict],
    decisions: list[dict],
    constraints: list[dict],
    locked_groups: list[list[str]] = (),
) -> tuple[list[dict], list[str], dict]:
    """Constrained union-find. Returns (clusters, decision ids of rejected bridges, metrics).

    A locked group is kept together and can neither gain nor lose members automatically.
    """
    must_not = defaultdict(set)
    must_link: list[tuple[str, str]] = []
    lock_of: dict[str, int] = {}
    present = set(row_order)
    for index, group in enumerate(locked_groups):
        members_present = [row_id for row_id in group if row_id in present]
        for row_id in members_present:
            lock_of[row_id] = index
        must_link.extend((members_present[0], other) for other in members_present[1:])
    for constraint in constraints:
        a, b = constraint["left_row_id"], constraint["right_row_id"]
        if constraint["kind"] == "must_not_link":
            must_not[a].add(b)
            must_not[b].add(a)
        elif constraint["kind"] == "must_link":
            must_link.append((a, b))

    auto_edges = sorted(
        (d for d in decisions if d["decision"] == "match" and d["right_row_id"] not in must_not[d["left_row_id"]]),
        key=lambda d: (-d["score"], d["left_row_id"], d["right_row_id"]),
    )
    adjacency = defaultdict(set)
    for d in auto_edges:
        adjacency[d["left_row_id"]].add(d["right_row_id"])
        adjacency[d["right_row_id"]].add(d["left_row_id"])

    parent = {row_id: row_id for row_id in row_order}
    members = {row_id: [row_id] for row_id in row_order}
    confidence = {row_id: 1.0 for row_id in row_order}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def compatible(ra: str, rb: str) -> bool:
        ids: dict[str, set] = defaultdict(set)
        houses: set[str] = set()
        for row_id in members[ra] + members[rb]:
            n = normalized[row_id]
            for column, value in n["identifier"].items():
                ids[column].add(value)
            if n.get("address") and n["address"]["house_number"]:
                houses.add(n["address"]["house_number"])
        if len(houses) > 1 or any(len(values) > 1 for values in ids.values()):
            return False
        if len({lock_of.get(row_id) for row_id in members[ra] + members[rb]}) > 1:
            return False
        small, large = sorted((ra, rb), key=lambda r: len(members[r]))
        large_set = set(members[large])
        return not any(must_not[row_id] & large_set for row_id in members[small])

    def union(ra: str, rb: str, score: float) -> None:
        if len(members[ra]) < len(members[rb]):
            ra, rb = rb, ra
        parent[rb] = ra
        members[ra].extend(members.pop(rb))
        confidence[ra] = min(confidence[ra], confidence.pop(rb), score)

    rejected_bridges: list[str] = []
    incompatible = 0
    for a, b in must_link:
        ra, rb = find(a), find(b)
        same_lock = a in lock_of and lock_of.get(a) == lock_of.get(b)
        if ra != rb and (same_lock or compatible(ra, rb)):
            union(ra, rb, 1.0)
        elif ra != rb:
            incompatible += 1
    for d in auto_edges:
        ra, rb = find(d["left_row_id"]), find(d["right_row_id"])
        if ra == rb:
            continue
        if not compatible(ra, rb):
            incompatible += 1
            continue
        if len(members[ra]) >= 2 and len(members[rb]) >= 2:
            other = set(members[rb])
            direct = sum(len(adjacency[row_id] & other) for row_id in members[ra])
            if direct < 2:
                rejected_bridges.append(d["id"])
                continue
        union(ra, rb, d["score"])

    position = {row_id: index for index, row_id in enumerate(row_order)}
    clusters = []
    for root, member_ids in members.items():
        ordered = sorted(member_ids, key=position.__getitem__)
        clusters.append({
            "id": "c_" + hashlib.sha1("|".join(sorted(ordered)).encode()).hexdigest()[:16],
            "member_row_ids": ordered,
            "confidence": round(confidence[root], 3),
            "status": "locked" if ordered[0] in lock_of else "active",
        })
    clusters.sort(key=lambda c: position[c["member_row_ids"][0]])
    sizes = [len(c["member_row_ids"]) for c in clusters]
    metrics = {
        "clusters": len(clusters),
        "multi_member_clusters": sum(1 for s in sizes if s > 1),
        "largest_cluster": max(sizes, default=0),
        "bridge_rejections": len(rejected_bridges),
        "incompatible_unions": incompatible,
    }
    return clusters, rejected_bridges, metrics


def canonicalize(clusters: list[dict], rows: dict[str, dict], normalized: dict[str, dict], locked_row_ids: set[str] = frozenset()) -> list[dict]:
    records = []
    for c in clusters:
        def rank(row_id: str) -> tuple:
            n, row = normalized[row_id], rows[row_id]
            verified = len(n["identifier"]) + len(n["phone"]) + len(n["email"])
            completeness = sum(1 for v in row["raw"].values() if v not in (None, ""))
            return (row_id not in locked_row_ids, -verified, -completeness, row["row_number"], row_id)

        ordered = sorted(c["member_row_ids"], key=rank)
        survivor = ordered[0]
        values, provenance, conflicts = {}, {}, {}
        columns = list(dict.fromkeys(col for row_id in ordered for col in rows[row_id]["raw"]))
        for column in columns:
            candidates = [(row_id, rows[row_id]["raw"].get(column)) for row_id in ordered]
            non_empty = [(row_id, v) for row_id, v in candidates if v not in (None, "")]
            if non_empty:
                chosen_row, chosen = non_empty[0]
                values[column] = chosen
                provenance[column] = {"row_id": chosen_row, "rule": "survivor" if chosen_row == survivor else "fill_from_member"}
                distinct = {str(v) for _, v in non_empty}
                if len(distinct) > 1:
                    conflicts[column] = [row_id for row_id, _ in non_empty]
            else:
                values[column] = ""
        records.append({
            "cluster_id": c["id"], "survivor_row_id": survivor, "values": values,
            "field_provenance": provenance, "conflicts": conflicts,
        })
    return records


def run(request: dict, progress: Callable[[str, dict], None] = lambda s, d: None, should_stop: Callable[[], bool] = lambda: False) -> dict:
    settings = validate_request(request)
    mapping = {col: role for col, role in request["mapping"].items() if role not in ("other", "ignore")}
    rows = {row["id"]: row for row in request["rows"]}
    row_order = [row["id"] for row in request["rows"]]

    progress("normalizing", {"rows": len(rows)})
    normalized = {row_id: normalize_row(row["raw"], mapping, settings["default_region"]) for row_id, row in rows.items()}
    unmatchable = sum(1 for n in normalized.values() if not (n["identifier"] or n["phone"] or n["email"] or n.get("address") or n.get("mailing_address") or n.get("url") or n.get("name")))
    if should_stop():
        return {"stopped_at": "normalizing"}

    progress("finding_candidates", {})
    pairs, block_metrics = generate_candidates(normalized, int(settings["max_block_size"]))
    progress("finding_candidates", {"candidate_pairs": len(pairs)})
    if should_stop():
        return {"stopped_at": "finding_candidates"}

    progress("evaluating_evidence", {"candidate_pairs": len(pairs)})
    decisions = []
    for index, ((left, right), block_ids) in enumerate(sorted(pairs.items())):
        if index % 5000 == 0 and should_stop():
            return {"stopped_at": "evaluating_evidence"}
        evidence, score, comparable = compare(normalized[left], normalized[right])
        decision, reason = decide(evidence, score, comparable, settings["strictness"])
        decisions.append({
            "id": "d_" + hashlib.sha1(f"{left}|{right}".encode()).hexdigest()[:16],
            "left_row_id": left, "right_row_id": right, "candidate_block_ids": sorted(set(block_ids)),
            "evidence": evidence, "score": round(score, 4), "decision": decision, "reason": reason,
        })

    progress("building_groups", {})
    clusters, bridges, cluster_metrics = cluster(row_order, normalized, decisions, request.get("constraints") or [], request.get("locked_groups") or [])
    bridge_set = set(bridges)
    for d in decisions:
        if d["id"] in bridge_set:
            d["decision"], d["reason"] = "possible_match", "Needs review: would bridge two existing groups with a single link"
    canonical = canonicalize(clusters, rows, normalized)

    counts = defaultdict(int)
    reasons = defaultdict(int)
    for d in decisions:
        counts[d["decision"]] += 1
        for item in d["evidence"]:
            if item["strength"] == "guard":
                reasons[item["explanation"]] += 1
    progress("creating_review_queue", {"possible_match": counts["possible_match"]})
    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "settings": settings,
        "decisions": decisions,
        "clusters": clusters,
        "canonical": canonical,
        "metrics": {
            "input_rows": len(rows), "unmatchable_rows": unmatchable, **block_metrics, **cluster_metrics,
            "decisions": dict(counts), "guard_reasons": dict(reasons),
        },
    }
