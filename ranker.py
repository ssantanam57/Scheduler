"""
Ranks schedule combinations by configurable criteria.

Criteria:
  free-days     — maximize days with zero classes (fewer class days = better)
  min-gaps      — minimize total idle time between consecutive classes on the same day
  no-early      — minimize early-morning slots (classes before 09:00)
  balanced      — weighted combo: free-days (50%) + min-gaps (30%) + no-early (20%)
"""
from typing import List
from models import Combination, Section, DAY_ORDER

EARLY_CUTOFF_MINUTES = 9 * 60  # 09:00


def _free_days_score(combo: Combination) -> float:
    """Higher is better: more days with no classes."""
    busy_days = set()
    for sec in combo.sections:
        busy_days |= sec.days_set()
    free = len(DAY_ORDER) - len(busy_days)
    return float(free)


def _gap_penalty(combo: Combination) -> float:
    """Lower is better: total idle minutes between classes on each day."""
    from models import TimeBlock
    day_blocks: dict[str, list] = {d: [] for d in DAY_ORDER}
    for sec in combo.sections:
        for block in sec.time_blocks:
            day_blocks[block.day].append((block.start_minutes(), block.end_minutes()))

    total_gap = 0
    for day, blocks in day_blocks.items():
        if len(blocks) < 2:
            continue
        blocks.sort()
        for i in range(1, len(blocks)):
            gap = blocks[i][0] - blocks[i - 1][1]
            if gap > 0:
                total_gap += gap
    return float(total_gap)


def _early_penalty(combo: Combination) -> float:
    """Lower is better: total early-morning minutes (classes starting before 09:00)."""
    penalty = 0
    for sec in combo.sections:
        for block in sec.time_blocks:
            start_min = block.start_minutes()
            if start_min < EARLY_CUTOFF_MINUTES:
                penalty += EARLY_CUTOFF_MINUTES - start_min
    return float(penalty)


def rank_combinations(
    combos: List[Combination],
    criterion: str = "free-days",
) -> List[Combination]:
    """
    Sort combinations by criterion (ascending for penalties, descending for rewards).
    Modifies combo.rank and combo.score in place.
    Returns the sorted list.
    """
    if not combos:
        return combos

    criterion = criterion.lower().replace("_", "-")

    if criterion == "free-days":
        scored = [(c, _free_days_score(c)) for c in combos]
        scored.sort(key=lambda x: -x[1])
    elif criterion in ("min-gaps", "gaps"):
        scored = [(c, _gap_penalty(c)) for c in combos]
        scored.sort(key=lambda x: x[1])
    elif criterion in ("no-early", "early"):
        scored = [(c, _early_penalty(c)) for c in combos]
        scored.sort(key=lambda x: x[1])
    elif criterion == "balanced":
        # Normalize each dimension to [0,1] then combine
        free_scores = [_free_days_score(c) for c in combos]
        gap_scores = [_gap_penalty(c) for c in combos]
        early_scores = [_early_penalty(c) for c in combos]

        def norm(vals, higher_better=True):
            mn, mx = min(vals), max(vals)
            if mx == mn:
                return [1.0] * len(vals)
            if higher_better:
                return [(v - mn) / (mx - mn) for v in vals]
            else:
                return [(mx - v) / (mx - mn) for v in vals]

        nf = norm(free_scores, higher_better=True)
        ng = norm(gap_scores, higher_better=False)
        ne = norm(early_scores, higher_better=False)

        combined = [0.5 * nf[i] + 0.3 * ng[i] + 0.2 * ne[i] for i in range(len(combos))]
        scored = list(zip(combos, combined))
        scored.sort(key=lambda x: -x[1])
    else:
        raise ValueError(
            f"Unknown criterion '{criterion}'. "
            "Choose: free-days, min-gaps, no-early, balanced"
        )

    for rank, (combo, score) in enumerate(scored, 1):
        combo.rank = rank
        combo.score = score

    return [c for c, _ in scored]
