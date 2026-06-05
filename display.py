"""
Rich-based display: tables, weekly grid panels, and CSV export.
"""
import csv
from typing import List, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.columns import Columns
from rich import box

from models import Combination, DAY_ORDER, DAY_NAMES

console = Console()

# One distinct Rich color per course slot (up to 8 courses)
SLOT_COLORS = [
    "bold cyan",
    "bold magenta",
    "bold green",
    "bold yellow",
    "bold blue",
    "bold red",
    "bold white",
    "bold orange1",
]

GRID_CHAR = "█"
EMPTY_CHAR = "·"

START_HOUR = 7
END_HOUR = 21
SLOT_MIN = 30


def _fmt_time(hhmm: int) -> str:
    h, m = divmod(hhmm, 100)
    return f"{h:02d}:{m:02d}"


def _format_schedule(section) -> str:
    if not section.time_blocks:
        return "Sin horario"
    by_time: dict = {}
    for b in section.time_blocks:
        key = (b.start, b.end)
        by_time.setdefault(key, []).append(DAY_NAMES.get(b.day, b.day))
    parts = []
    for (s, e), days in sorted(by_time.items()):
        parts.append(f"{'/'.join(days)} {s:04d}-{e:04d}")
    return "  ".join(parts)


def print_combination(combo: Combination, show_rank: bool = True) -> None:
    title = f"Opción #{combo.rank}  •  score {combo.score:.3f}"

    # ── Section table ──────────────────────────────────────────────────────
    tbl = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold white on grey23",
        expand=True,
        padding=(0, 1),
        min_width=80,
    )
    tbl.add_column("NRC", style="dim", no_wrap=True, width=7)
    tbl.add_column("Curso", ratio=3)
    tbl.add_column("Horario", ratio=3)
    tbl.add_column("Profesor", ratio=3)
    tbl.add_column("Cupos", justify="right", no_wrap=True, width=6)

    for idx, sec in enumerate(combo.sections):
        color = SLOT_COLORS[idx % len(SLOT_COLORS)]
        label = Text(f"{sec.course_code} sec.{sec.section_num}", style=color)
        label.append(f"\n{sec.title[:30]}", style="white dim")
        tbl.add_row(
            sec.nrc,
            label,
            _format_schedule(sec),
            sec.professor or "—",
            str(sec.seats_avail),
        )

    # ── Weekly grid ────────────────────────────────────────────────────────
    grid_lines = _build_grid(combo)

    panel = Panel(
        _join(tbl, grid_lines),
        title=f"[bold]{title}[/bold]",
        border_style="grey46",
        expand=True,
    )
    console.print(panel)


def _join(tbl: Table, grid_text: str):
    """Combine table + grid into a single renderable group."""
    from rich.console import Group
    return Group(tbl, Text.from_ansi(grid_text))


def _build_grid(combo: Combination) -> str:
    total_slots = (END_HOUR - START_HOUR) * 60 // SLOT_MIN
    day_keys = DAY_ORDER[:6]  # Mon–Sat

    # slot -> day -> (char, ansi_color_index)
    grid: dict[str, list] = {d: [None] * total_slots for d in day_keys}

    for idx, sec in enumerate(combo.sections):
        char = GRID_CHAR
        # ANSI 256 color: cycle through a visible palette
        colors = [196, 33, 46, 220, 201, 208, 51, 118]
        ansi = colors[idx % len(colors)]
        for block in sec.time_blocks:
            if block.day not in grid:
                continue
            s_slot = (block.start_minutes() - START_HOUR * 60) // SLOT_MIN
            e_slot = (block.end_minutes() - START_HOUR * 60) // SLOT_MIN
            for sl in range(max(0, s_slot), min(total_slots, e_slot)):
                grid[block.day][sl] = (char, ansi)

    # Build the text grid
    day_labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb"]
    lines = []
    header = "       " + "  ".join(f"{d:^3}" for d in day_labels)
    lines.append(header)
    lines.append("       " + "───  " * len(day_labels))

    for slot in range(total_slots):
        total_min = START_HOUR * 60 + slot * SLOT_MIN
        t = f"{total_min // 60:02d}:{total_min % 60:02d}"
        cells = []
        for d in day_keys:
            cell = grid[d][slot]
            if cell:
                char, ansi = cell
                cells.append(f"\x1b[38;5;{ansi}m{char}\x1b[0m  ")
            else:
                cells.append(f"\x1b[38;5;240m{EMPTY_CHAR}\x1b[0m  ")
        lines.append(f"{t}  " + " ".join(cells))

    # Legend
    lines.append("")
    colors_ansi = [196, 33, 46, 220, 201, 208, 51, 118]
    for idx, sec in enumerate(combo.sections):
        ansi = colors_ansi[idx % len(colors_ansi)]
        sched_str = _format_schedule(sec)
        lines.append(
            f"  \x1b[38;5;{ansi}m{GRID_CHAR}\x1b[0m  "
            f"{sec.course_code} sec.{sec.section_num} — {sec.title[:36]}  [{sched_str}]"
        )

    return "\n".join(lines)


def print_summary(
    combos: List[Combination],
    top_n: int = 5,
    criterion: str = "free-days",
) -> None:
    total = len(combos)
    console.print(f"\n[bold green]{total}[/bold green] combinación(es) válida(s)  •  criterio: [bold]{criterion}[/bold]\n")
    for combo in combos[:top_n]:
        print_combination(combo)


def export_csv(combos: List[Combination], path: str, top_n: Optional[int] = None) -> None:
    rows = []
    subset = combos[:top_n] if top_n else combos
    for combo in subset:
        for sec in combo.sections:
            rows.append({
                "rank": combo.rank,
                "score": f"{combo.score:.4f}",
                "nrc": sec.nrc,
                "course_code": sec.course_code,
                "section": sec.section_num,
                "title": sec.title,
                "credits": sec.credits,
                "professor": sec.professor,
                "schedule": _format_schedule(sec),
                "campus": sec.campus,
                "seats_available": sec.seats_avail,
            })

    if not rows:
        console.print("[yellow]No hay combinaciones para exportar.[/yellow]")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    console.print(f"\n[bold green]✓[/bold green] CSV exportado → [cyan]{path}[/cyan]  ({len(rows)} filas, {len(subset)} combinaciones)")
