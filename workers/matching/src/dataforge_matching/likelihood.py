"""How likely each candidate pair is the same record, estimated from the dataset itself (Fellegi-Sunter).

For each field, the chance it agrees by coincidence (u) comes from random pairs of records. For an exact agreement,
u is how often that very value occurs (term frequency): a phone shared by a household counts less than a phone no one
else has, and "Smith" less than "Lovelace". The chance a field agrees when two records are the same record (m) is
estimated by expectation-maximization: the pairs the rules merged count as duplicates, the pairs they kept apart as
distinct, and each pair waiting for review by how likely it currently looks. Each field adds log2(m/u) bits; with the
share of pairs that are duplicates as the prior, the bits become a probability.

The likelihood orders and annotates review. It never merges anything by itself.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from itertools import combinations
from typing import Callable

LIKELIHOOD_VERSION = "fellegi-sunter-em-tf-1"
LEVELS = ("exact", "similar", "initial", "different", "conflict")
# Used when too few pairs were merged with certainty to learn from.
DEFAULT_M = {"exact": 0.85, "similar": 0.08, "initial": 0.03, "different": 0.03, "conflict": 0.01}
RANDOM_PAIRS = 20_000
MIN_MATCHES = 20
MAX_BITS = 20.0
EM_ROUNDS = 8
# How strongly the default reliabilities hold against what the data suggests (in pairs).
PSEUDO_COUNT = 10.0


def _level(item: dict) -> str:
    if item["field"] == "name" and item["result"] != "conflict":
        # An initial ("G." and "Grace") is its own level: it fits the same person and their relatives alike,
        # so how much it is worth is learned from the data rather than assumed.
        if "first initial agrees" in item["explanation"]:
            return "initial"
        return "exact" if item["similarity"] >= 0.97 else "similar" if item["similarity"] >= 0.8 else "different"
    return item["result"] if item["result"] in LEVELS else "different"


def _levels(evidence: list[dict]) -> dict[str, str]:
    return {item["field"]: _level(item) for item in evidence}


def agrees_throughout(evidence: list[dict]) -> bool:
    """No compared field disagrees. Required, besides a high likelihood, before a pair is decided together with others."""
    return all(_level(item) not in ("different", "conflict") for item in evidence)


def _probability(counts: Counter, present: int, level: str, alpha: float) -> float:
    return (counts[level] + alpha) / (present + alpha * len(LEVELS))


def _posterior(prior: float, fields: list[tuple[str, str, float]], m_table: dict[str, dict[str, float]]) -> float:
    bits = prior
    for field, level, u in fields:
        m = m_table.get(field, DEFAULT_M).get(level, DEFAULT_M[level])
        bits += max(-MAX_BITS, min(MAX_BITS, math.log2(m / u)))
    return 1.0 / (1.0 + 2.0 ** -max(-60.0, min(60.0, bits)))


def annotate(
    decisions: list[dict],
    normalized: dict[str, dict],
    compare: Callable[[dict, dict], list[dict]],
    seed: int = 0,
) -> dict:
    """Set ``likelihood`` (0-1) on every decision. ``compare(left, right)`` returns field evidence like the engine's.
    Returns the estimated parameters for the run's metrics."""
    ids = sorted(normalized)
    count = len(ids)
    if count < 2 or not decisions:
        for decision in decisions:
            decision["likelihood"] = None
        return {"version": LIKELIHOOD_VERSION, "estimated": False}

    # Chance agreement from random pairs (all pairs when there are few); deterministic for the same rows.
    all_pairs = count * (count - 1) // 2
    if all_pairs <= RANDOM_PAIRS:
        sample = combinations(ids, 2)
    else:
        rng = random.Random(seed or count)
        sample = (tuple(rng.sample(ids, 2)) for _ in range(RANDOM_PAIRS))
    u_counts: dict[str, Counter] = defaultdict(Counter)
    for left, right in sample:
        for field, level in _levels(compare(normalized[left], normalized[right])).items():
            u_counts[field][level] += 1

    frequency: dict[str, Counter] = defaultdict(Counter)
    for n in normalized.values():
        frequency["phone"].update(n["phone"])
        frequency["email"].update(n["email"])
        if n.get("address"):
            frequency["address"][n["address"]["full"]] += 1
        if n.get("person"):
            frequency["surname"][n["person"][1]] += 1
            frequency["given"][n["person"][0][0]] += 1
        elif n.get("name"):
            frequency["name"][n["name"]] += 1

    def value_frequency(field: str, left: dict, right: dict) -> float | None:
        """How often the agreeing value itself occurs, for an exact agreement."""
        if field in ("phone", "email"):
            shared = left[field] & right[field]
            return max(frequency[field][value] for value in shared) / count if shared else None
        if field == "address" and left.get("address") and right.get("address") and left["address"]["full"] == right["address"]["full"]:
            return frequency["address"][left["address"]["full"]] / count
        if field == "name":
            if left.get("person") and right.get("person") and left["person"][1] == right["person"][1]:
                surname = frequency["surname"][left["person"][1]] / count
                same_given = left["person"][0][0] == right["person"][0][0]
                return surname * (frequency["given"][left["person"][0][0]] / count if same_given else 1.0)
            if left.get("name") and left.get("name") == right.get("name") and not left.get("person"):
                return frequency["name"][left["name"]] / count
        return None

    # Per pair: the level of each field and, for exact agreements, how often the agreeing value occurs.
    observed = []
    for decision in decisions:
        left, right = normalized[decision["left_row_id"]], normalized[decision["right_row_id"]]
        fields = []
        for field, level in _levels(decision["evidence"]).items():
            u_field = u_counts.get(field, Counter())
            u = _probability(u_field, sum(u_field.values()), level, 0.5)
            if level == "exact" and (tf := value_frequency(field, left, right)) is not None:
                u = max(tf, 1.0 / count)
            fields.append((field, level, u))
        observed.append(fields)

    # Field reliability (m) and the share of duplicates (prior) by expectation-maximization. The rules' certain
    # decisions are labels and stay fixed; only pairs in review are re-estimated. Learning from the merged pairs alone
    # would teach that duplicates never disagree on anything, because a disagreement is what keeps a pair from being
    # merged with certainty; letting the rejected pairs float would teach that household members are duplicates.
    label = {"match": 1.0, "possible_match": None}
    fixed = [label.get(d["decision"], 0.0) for d in decisions]
    weights = [0.5 if value is None else value for value in fixed]
    learned = sum(1 for value in fixed if value == 1.0) >= MIN_MATCHES
    m_table: dict[str, dict[str, float]] = {}
    for _ in range(EM_ROUNDS if learned else 1):
        if learned:
            m_mass: dict[str, Counter] = defaultdict(Counter)
            for weight, fields in zip(weights, observed):
                for field, level, _u in fields:
                    m_mass[field][level] += weight
            m_table = {
                field: {level: (mass[level] + PSEUDO_COUNT * DEFAULT_M[level]) / (sum(mass.values()) + PSEUDO_COUNT) for level in LEVELS}
                for field, mass in m_mass.items()
            }
        share = max(sum(weights), 1.0) / all_pairs
        prior = math.log2(share) - math.log2(max(1.0 - share, 1e-12))
        weights = [_posterior(prior, fields, m_table) if value is None else value for value, fields in zip(fixed, observed)]
    # Every pair is annotated with the model's own view, the rules' certain decisions included.
    for decision, fields in zip(decisions, observed):
        decision["likelihood"] = round(_posterior(prior, fields, m_table), 4)
    field_weights = {
        field: {level: round(math.log2(m_table.get(field, DEFAULT_M).get(level, DEFAULT_M[level]) / _probability(u_counts.get(field, Counter()), sum(u_counts.get(field, Counter()).values()), level, 0.5)), 2) for level in LEVELS}
        for field in sorted(u_counts)
    }
    return {"version": LIKELIHOOD_VERSION, "estimated": True, "learned_from_matches": learned, "random_pairs": min(all_pairs, RANDOM_PAIRS), "field_weights": field_weights}
