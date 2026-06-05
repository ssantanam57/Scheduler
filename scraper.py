"""
Fetches course sections from the Uniandes course catalog API.
The site uses a public REST API — no browser rendering required.
"""
import re
import requests
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from models import Section, TimeBlock, DAY_ORDER

BASE_URL = "https://ofertadecursos.uniandes.edu.co/api"
DEFAULT_TERM = "202620"


def _parse_section(raw: dict) -> Section:
    blocks = []
    for sched in raw.get("schedules", []):
        t_ini = int(sched.get("time_ini") or 0)
        t_fin = int(sched.get("time_fin") or 0)
        if not t_ini and not t_fin:
            continue
        for day_key in DAY_ORDER:
            if sched.get(day_key):
                blocks.append(TimeBlock(day=day_key, start=t_ini, end=t_fin))

    instructors = raw.get("instructors", [])
    primary = next((i["name"] for i in instructors if i.get("ind") == "Y"), None)
    if primary is None and instructors:
        primary = instructors[0].get("name", "")
    professor = (primary or "").strip().title()

    return Section(
        nrc=raw.get("nrc", ""),
        prefix=raw.get("class", ""),
        course_num=raw.get("course", ""),
        section_num=raw.get("section", ""),
        title=raw.get("title", "").strip().title(),
        credits=int(raw.get("credits") or 0),
        professor=professor,
        campus=raw.get("campus", "").strip(),
        time_blocks=blocks,
        seats_avail=int(raw.get("seatsavail") or 0),
    )


def _fetch_raw(
    query: str,
    term: str,
    max_results: int,
    sess: requests.Session,
) -> List[Section]:
    sections = []
    offset = 0
    page_size = 50

    while True:
        params = {
            "term": term,
            "nameInput": query,
            "offset": offset,
            "limit": page_size,
            "ptrm": "", "prefix": "", "attr": "", "campus": "",
            "attrs": "", "timeStart": "", "courseQuotas": "", "days": "",
            "courseRestrictions": "", "programNew": "", "profesorName": "",
        }
        try:
            resp = sess.get(f"{BASE_URL}/courses", params=params, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(f"API request failed for '{query}': {e}") from e

        batch = resp.json()
        if not batch:
            break
        for raw in batch:
            sections.append(_parse_section(raw))
        if len(batch) < page_size:
            break
        offset += page_size
        if len(sections) >= max_results:
            break

    return sections


def fetch_sections(
    query: str,
    term: str = DEFAULT_TERM,
    max_results: int = 200,
    session: Optional[requests.Session] = None,
) -> List[Section]:
    """Fetch all sections matching query (NRC, course code, or name fragment)."""
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (compatible; schedule-optimizer/1.0)"})
    return _fetch_raw(query, term, max_results, sess)


def _pair_sections(magistral: Section, complementary: Section) -> Section:
    """Create a synthetic section that holds both magistral and complementary time blocks."""
    from copy import deepcopy
    merged = deepcopy(magistral)
    merged.nrc = f"{magistral.nrc}+{complementary.nrc}"
    merged.section_num = f"{magistral.section_num}+{complementary.section_num}"
    merged.time_blocks = magistral.time_blocks + complementary.time_blocks
    return merged


@dataclass
class CourseBundle:
    """
    A course that may have a paired complementaria.

    magistral_sections: sections of the main lecture course
    complementary_map:  letter -> [complementary Section, ...]
                        empty dict means no complementaria exists
    has_complementary:  True when the course has a XXXC companion
    """
    code: str                                     # e.g. "MATE-1203"
    title: str
    magistral_sections: List[Section] = field(default_factory=list)
    complementary_map: Dict[str, List[Section]] = field(default_factory=dict)

    @property
    def has_complementary(self) -> bool:
        return bool(self.complementary_map)

    def sections_for_letter(self, letter: str) -> Tuple[Section, List[Section]]:
        """Return (magistral_section, [complementary_options]) for a given letter."""
        mag = next((s for s in self.magistral_sections if s.section_num == letter), None)
        comps = self.complementary_map.get(letter, [])
        return mag, comps

    def all_section_options(self) -> List[Section]:
        """
        Return every selectable option as a flat list of Section objects.

        Standalone (numeric) sections are returned as-is.
        Letter sections that require a complementaria are returned as MERGED
        sections — one per complementary sub-section — where time_blocks
        combine both the magistral and complementary blocks.

        e.g. MATE-2711 yields: [sec1, sec2, sec_A+A1, sec_A+A2, sec_A+A3]
        """
        options: List[Section] = []
        for sec in self.magistral_sections:
            if sec.section_num in self.complementary_map:
                for comp in self.complementary_map[sec.section_num]:
                    options.append(_pair_sections(sec, comp))
            else:
                options.append(sec)
        return options


def fetch_course_bundle(
    query: str,
    term: str = DEFAULT_TERM,
    session: Optional[requests.Session] = None,
) -> Dict[str, "CourseBundle"]:
    """
    Fetch all sections for a course query.
    Returns a dict of course_code -> CourseBundle.

    Automatically detects complementarias (XXXC courses) and links them
    to the corresponding magistral letter sections.
    """
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (compatible; schedule-optimizer/1.0)"})

    all_sections = _fetch_raw(query, term, max_results=300, sess=sess)

    # Group by course code
    by_code: Dict[str, List[Section]] = {}
    for sec in all_sections:
        by_code.setdefault(sec.course_code, []).append(sec)

    # Identify complementary courses (code ends in C, and base code exists)
    bundles: Dict[str, CourseBundle] = {}
    comp_codes: set = set()

    for code, secs in by_code.items():
        # Complementary codes end with "C" (e.g. MATE-1203C)
        if code.endswith("C") and code[:-1] in by_code:
            comp_codes.add(code)

    for code, secs in by_code.items():
        if code in comp_codes:
            continue  # handled below when processing the base course

        comp_code = code + "C"
        comp_secs = by_code.get(comp_code, [])

        # Build complementary_map: letter -> [Section, ...]
        comp_map: Dict[str, List[Section]] = {}
        if comp_secs:
            for cs in comp_secs:
                # Section names like "A1", "B2" — first char is the letter
                letter = cs.section_num[0].upper() if cs.section_num else ""
                if letter:
                    comp_map.setdefault(letter, []).append(cs)

        bundles[code] = CourseBundle(
            code=code,
            title=secs[0].title if secs else "",
            magistral_sections=secs,
            complementary_map=comp_map,
        )

    return bundles


def get_terms(session: Optional[requests.Session] = None) -> List[dict]:
    sess = session or requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (compatible; schedule-optimizer/1.0)"})
    resp = sess.get(f"{BASE_URL}/terms", timeout=10)
    resp.raise_for_status()
    return resp.json()
