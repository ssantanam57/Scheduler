"""
Generates all valid (conflict-free) schedule combinations from a list of course section groups.
"""
import itertools
from typing import List, Dict
from models import Section, Combination


def find_valid_combinations(
    course_sections: Dict[str, List[Section]],
    max_combinations: int = 10000,
) -> List[Combination]:
    """
    Given a dict mapping course_label -> [sections], return all conflict-free combinations.
    One section is chosen per course; combinations where any two sections overlap are dropped.
    """
    if not course_sections:
        return []

    labels = list(course_sections.keys())
    section_lists = [course_sections[lbl] for lbl in labels]

    valid: List[Combination] = []

    for combo in itertools.product(*section_lists):
        if _has_conflict(combo):
            continue
        valid.append(Combination(sections=list(combo)))
        if len(valid) >= max_combinations:
            break

    return valid


def _has_conflict(sections) -> bool:
    sections = list(sections)
    for i in range(len(sections)):
        for j in range(i + 1, len(sections)):
            if sections[i].conflicts_with(sections[j]):
                return True
    return False
