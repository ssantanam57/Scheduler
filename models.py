from dataclasses import dataclass, field
from typing import List, Optional


DAY_NAMES = {
    "l": "Lun",
    "m": "Mar",
    "i": "Mié",  # "I" = Miércoles per site nomenclature
    "j": "Jue",
    "v": "Vie",
    "s": "Sáb",
    "d": "Dom",
}

DAY_ORDER = ["l", "m", "i", "j", "v", "s", "d"]


@dataclass
class TimeBlock:
    day: str          # single lowercase key: 'l','m','i','j','v','s','d'
    start: int        # HHMM as int, e.g. 630, 1530
    end: int          # HHMM as int

    def overlaps(self, other: "TimeBlock") -> bool:
        if self.day != other.day:
            return False
        return self.start < other.end and other.start < self.end

    def duration_minutes(self) -> int:
        sh, sm = divmod(self.start, 100)
        eh, em = divmod(self.end, 100)
        return (eh * 60 + em) - (sh * 60 + sm)

    def start_minutes(self) -> int:
        h, m = divmod(self.start, 100)
        return h * 60 + m

    def end_minutes(self) -> int:
        h, m = divmod(self.end, 100)
        return h * 60 + m

    def __str__(self) -> str:
        return f"{DAY_NAMES.get(self.day, self.day)} {self.start:04d}-{self.end:04d}"


@dataclass
class Section:
    nrc: str
    prefix: str       # e.g. "ISIS"
    course_num: str   # e.g. "1221"
    section_num: str
    title: str
    credits: int
    professor: str
    campus: str
    time_blocks: List[TimeBlock] = field(default_factory=list)
    seats_avail: int = 0

    @property
    def course_code(self) -> str:
        return f"{self.prefix}-{self.course_num}"

    def conflicts_with(self, other: "Section") -> bool:
        for b1 in self.time_blocks:
            for b2 in other.time_blocks:
                if b1.overlaps(b2):
                    return True
        return False

    def days_set(self) -> set:
        return {b.day for b in self.time_blocks}


@dataclass
class Combination:
    sections: List[Section]
    score: float = 0.0
    rank: int = 0
