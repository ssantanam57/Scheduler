"""
Web server for the Uniandes schedule optimizer.
Run: python3 app.py  →  open http://localhost:8000
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scheduler"))

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, List, Optional
import requests as req_lib

from scraper import get_terms, fetch_course_bundle, DEFAULT_TERM
from optimizer import find_valid_combinations
from ranker import rank_combinations
from models import Section, TimeBlock, DAY_ORDER, DAY_NAMES

app = FastAPI(title="Uniandes Horarios", docs_url=None, redoc_url=None)

_http = req_lib.Session()
_http.headers.update({"User-Agent": "Mozilla/5.0 (compatible; schedule-optimizer/1.0)"})


# ── Serialization helpers ────────────────────────────────────────────────────

def _sec_to_dict(sec: Section) -> dict:
    return {
        "nrc": sec.nrc,
        "prefix": sec.prefix,
        "course_num": sec.course_num,
        "section_num": sec.section_num,
        "title": sec.title,
        "credits": sec.credits,
        "professor": sec.professor,
        "campus": sec.campus,
        "seats_avail": sec.seats_avail,
        "time_blocks": [
            {"day": b.day, "start": b.start, "end": b.end}
            for b in sec.time_blocks
        ],
    }


def _dict_to_sec(d: dict) -> Section:
    blocks = [
        TimeBlock(day=b["day"], start=b["start"], end=b["end"])
        for b in d.get("time_blocks", [])
    ]
    return Section(
        nrc=d.get("nrc", ""),
        prefix=d.get("prefix", ""),
        course_num=d.get("course_num", ""),
        section_num=d.get("section_num", ""),
        title=d.get("title", ""),
        credits=int(d.get("credits") or 0),
        professor=d.get("professor", ""),
        campus=d.get("campus", ""),
        time_blocks=blocks,
        seats_avail=int(d.get("seats_avail") or 0),
    )


# ── API routes ───────────────────────────────────────────────────────────────

@app.get("/api/terms")
def api_terms():
    try:
        return get_terms(session=_http)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/search")
def api_search(q: str, term: str = DEFAULT_TERM):
    """
    Search for a course.  Returns a list of matching CourseBundle summaries,
    each with an `options` array of selectable sections (standalone + paired).
    """
    if not q.strip():
        return []
    try:
        bundles = fetch_course_bundle(q.strip(), term=term, session=_http)
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    import re
    q_up = q.upper().replace(" ", "-")
    if re.match(r"^[A-Z]{2,6}-[A-Z0-9]+$", q_up) and q_up in bundles:
        bundles = {q_up: bundles[q_up]}

    result = []
    for code, bundle in sorted(bundles.items()):
        options = bundle.all_section_options()
        result.append({
            "code": code,
            "title": bundle.title,
            "has_complementary": bundle.has_complementary,
            "options": [_sec_to_dict(s) for s in options],
        })
    return result


class OptimizeRequest(BaseModel):
    # course_groups: label -> list of Section dicts (the options to choose from)
    course_groups: Dict[str, List[dict]]
    criterion: str = "free-days"
    top_n: int = 5


@app.post("/api/optimize")
def api_optimize(req: OptimizeRequest):
    if not req.course_groups:
        raise HTTPException(400, "No hay cursos seleccionados.")

    groups: Dict[str, List[Section]] = {
        label: [_dict_to_sec(s) for s in secs]
        for label, secs in req.course_groups.items()
        if secs
    }

    combos = find_valid_combinations(groups, max_combinations=10000)
    if not combos:
        return {"total": 0, "combinations": []}

    try:
        ranked = rank_combinations(combos, criterion=req.criterion)
    except ValueError as e:
        raise HTTPException(400, str(e))

    top = ranked[: req.top_n]

    def _combo_to_dict(c):
        return {
            "rank": c.rank,
            "score": round(c.score, 4),
            "sections": [_sec_to_dict(s) for s in c.sections],
        }

    return {"total": len(ranked), "combinations": [_combo_to_dict(c) for c in top]}


# ── Static files (SPA) ───────────────────────────────────────────────────────

_static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    return FileResponse(os.path.join(_static_dir, "index.html"))


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print("\n  🗓  Uniandes Horarios")
    print("  ─────────────────────────────────────────")
    print(f"  Abre tu navegador en  http://localhost:{port}")
    print("  Presiona Ctrl+C para detener el servidor.\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
