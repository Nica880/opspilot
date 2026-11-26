"""Pure metric functions, unit-tested."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return float(ordered[idx])


@dataclass(frozen=True)
class F1:
    micro: float
    macro: float
    n: int
    per_label: dict[str, float]


def _f1(tp: int, fp: int, fn: int) -> float:
    if tp == 0:
        return 0.0
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def label_f1(pairs: Iterable[tuple[set[str], set[str]]], vocabulary: Iterable[str]) -> F1:
    """Multi-label F1 over (gold, predicted) pairs, restricted to `vocabulary`.

    Macro averages only labels that occur in gold or predictions, so absent labels do not
    inflate or deflate the score.
    """
    vocab = set(vocabulary)
    counts: dict[str, list[int]] = {label: [0, 0, 0] for label in vocab}
    n = 0
    for gold, pred in pairs:
        gold, pred = gold & vocab, pred & vocab
        n += 1
        for label in gold & pred:
            counts[label][0] += 1
        for label in pred - gold:
            counts[label][1] += 1
        for label in gold - pred:
            counts[label][2] += 1
    tp = sum(c[0] for c in counts.values())
    fp = sum(c[1] for c in counts.values())
    fn = sum(c[2] for c in counts.values())
    per_label = {label: _f1(*c) for label, c in counts.items() if any(c)}
    macro = sum(per_label.values()) / len(per_label) if per_label else 0.0
    return F1(micro=_f1(tp, fp, fn), macro=macro, n=n, per_label=per_label)


def recall_at_k(ranks: Sequence[int | None], k: int) -> float:
    if not ranks:
        return 0.0
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)


def mrr(ranks: Sequence[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r is not None) / len(ranks)


def rank_of(target: str, ranked: Sequence[str]) -> int | None:
    return ranked.index(target) + 1 if target in ranked else None
