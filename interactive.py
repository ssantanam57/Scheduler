"""
Interactive TUI for the Uniandes schedule optimizer.
Launched when running `python3 main.py` with no arguments.
"""
import sys
import re
import requests
import questionary
from questionary import Style

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from scraper import fetch_course_bundle, fetch_sections, get_terms, DEFAULT_TERM, CourseBundle
from models import Section
from optimizer import find_valid_combinations
from ranker import rank_combinations
from display import print_summary, export_csv, console

STYLE = Style([
    ("qmark",       "fg:#00d7ff bold"),
    ("question",    "bold"),
    ("answer",      "fg:#00ff87 bold"),
    ("pointer",     "fg:#00d7ff bold"),
    ("highlighted", "fg:#00d7ff bold"),
    ("selected",    "fg:#00ff87"),
    ("separator",   "fg:#555555"),
    ("instruction", "fg:#888888"),
    ("text",        ""),
])


def _banner():
    console.print(Panel(
        "[bold cyan]Optimizador de Horarios[/bold cyan]\n"
        "[dim]Universidad de los Andes[/dim]",
        expand=False,
        border_style="cyan",
    ))


def _pick_term(session: requests.Session) -> str:
    terms = get_terms(session)
    choices = [f"{t['term_desc']}  —  {t['term_name']}" for t in terms]
    answer = questionary.select(
        "Período académico:",
        choices=choices,
        style=STYLE,
    ).ask()
    if not answer:
        sys.exit(0)
    return answer.split()[0]


def _pick_courses(term: str, session: requests.Session) -> dict:
    """
    Interactively collect courses. Handles complementary (magistral + XXXC) pairs.
    Returns: {display_label: [Section, ...]}
    """
    course_groups: dict = {}

    console.print("\n[bold]Agrega los cursos uno por uno.[/bold] "
                  "[dim](Escribe el código como ISIS-1221 o un fragmento del nombre)[/dim]")

    while True:
        query = questionary.text(
            "Código / nombre del curso (Enter vacío para terminar):",
            style=STYLE,
        ).ask()

        if query is None:
            sys.exit(0)
        query = query.strip()
        if not query:
            if not course_groups:
                console.print("[yellow]Agrega al menos un curso.[/yellow]")
                continue
            break

        # Fetch
        with console.status(f"Buscando '{query}'…"):
            try:
                bundles = fetch_course_bundle(query, term=term, session=session)
            except RuntimeError as e:
                console.print(f"[red]Error:[/red] {e}")
                continue

        if not bundles:
            console.print(f"[yellow]Sin resultados para '{query}'.[/yellow]")
            continue

        # Exact match if query looks like a course code
        query_upper = query.upper().replace(" ", "-")
        is_code = bool(re.match(r"^[A-Z]{2,6}-[A-Z0-9]+$", query_upper))
        if is_code and query_upper in bundles:
            bundles = {query_upper: bundles[query_upper]}

        # If multiple courses matched, let user pick one
        if len(bundles) > 1:
            choices = [
                f"{code}  —  {b.title}  ({len(b.magistral_sections)} secciones magistrales)"
                for code, b in sorted(bundles.items())
            ]
            chosen = questionary.select(
                f"'{query}' coincide con varios cursos. ¿Cuál?",
                choices=choices,
                style=STYLE,
            ).ask()
            if not chosen:
                continue
            chosen_code = chosen.split()[0]
            bundles = {chosen_code: bundles[chosen_code]}

        code, bundle = next(iter(bundles.items()))

        all_opts = bundle.all_section_options()
        if bundle.has_complementary:
            result = _pick_sections(bundle, all_opts)
            if result:
                course_groups.update(result)
        else:
            label = f"{code} — {bundle.title}"
            console.print(f"  [green]✓[/green] {len(all_opts)} sección(es) en {code}")
            course_groups[label] = all_opts

        # Show current list
        if course_groups:
            console.print(f"\n[dim]Cursos seleccionados hasta ahora:[/dim]")
            for lbl, secs in course_groups.items():
                n = len(secs)
                console.print(f"  [cyan]·[/cyan] {lbl} [dim]({n} opciones)[/dim]")

    return course_groups


def _pick_sections(bundle: CourseBundle, all_opts: list) -> dict:
    """
    For a course that has some sections with complementaria and some without:
    Show ALL individual options (standalone + merged magistral+comp pairs),
    let the user check which ones to include, or take all.
    """
    console.print(
        f"\n[bold yellow]{bundle.code}[/bold yellow] tiene "
        f"{len(all_opts)} opción(es) "
        f"({'algunas con complementaria' if bundle.has_complementary else 'sin complementaria'})."
    )

    choices = [
        questionary.Choice(
            title="✦  Incluir TODAS (el optimizador elige la mejor)",
            value="__all__",
        )
    ]
    for opt in all_opts:
        sched = _fmt_section_sched(opt)
        tag = " [dim][+comp][/dim]" if "+" in opt.section_num else ""
        choices.append(questionary.Choice(
            title=f"Sec. {opt.section_num:6s}  {sched:30s}  {opt.professor or '—'}{tag}",
            value=opt.section_num,
        ))

    chosen = questionary.select(
        f"¿Qué sección(es) de {bundle.code} incluir?",
        choices=choices,
        style=STYLE,
    ).ask()

    if not chosen:
        return {}

    label = f"{bundle.code} — {bundle.title}"
    if chosen == "__all__":
        console.print(f"  [green]✓[/green] {len(all_opts)} opción(es) en {bundle.code}")
        return {label: all_opts}

    selected = [o for o in all_opts if o.section_num == chosen]
    console.print(f"  [green]✓[/green] Sec. {chosen} fija en {bundle.code}")
    return {label: selected}


def _fmt_section_sched(sec: Section) -> str:
    if not sec.time_blocks:
        return "(sin horario)"
    from models import DAY_NAMES
    by_time: dict = {}
    for b in sec.time_blocks:
        by_time.setdefault((b.start, b.end), []).append(DAY_NAMES.get(b.day, b.day))
    parts = [f"{'/'.join(days)} {s:04d}-{e:04d}" for (s, e), days in sorted(by_time.items())]
    return "  ".join(parts)


def _pick_rank() -> str:
    labels = {
        "free-days": "Maximizar días libres",
        "min-gaps": "Minimizar brechas entre clases",
        "no-early": "Evitar clases temprano (antes de 9am)",
        "balanced": "Equilibrado (mezcla de los tres)",
    }
    choices = [questionary.Choice(title=f"{v}  [{k}]", value=k) for k, v in labels.items()]
    answer = questionary.select(
        "Criterio de ranking:",
        choices=choices,
        style=STYLE,
    ).ask()
    return answer or "free-days"


def _pick_top() -> int:
    answer = questionary.text(
        "¿Cuántas opciones mostrar? [default: 5]",
        default="5",
        style=STYLE,
        validate=lambda v: v.isdigit() and int(v) > 0 or "Ingresa un número positivo",
    ).ask()
    return int(answer or "5")


def run_interactive() -> None:
    _banner()

    session = requests.Session()

    # 1. Pick term
    term = _pick_term(session)
    console.print(f"  [green]✓[/green] Período [bold]{term}[/bold]\n")

    # 2. Pick courses (handles complementaries)
    course_groups = _pick_courses(term, session)

    if not course_groups:
        console.print("[red]No se agregaron cursos válidos.[/red]")
        sys.exit(1)

    # 3. Pick ranking
    criterion = _pick_rank()

    # 4. Pick top N
    top_n = _pick_top()

    # 5. Optimize
    console.print(f"\n[dim]Generando combinaciones…[/dim]")
    combos = find_valid_combinations(course_groups, max_combinations=10000)

    if not combos:
        console.print("\n[red]❌ No hay combinaciones válidas (todos los horarios se cruzan).[/red]")
        sys.exit(0)

    ranked = rank_combinations(combos, criterion=criterion)
    print_summary(ranked, top_n=top_n, criterion=criterion)

    # 6. Optional CSV export
    want_csv = questionary.confirm(
        "¿Exportar resultados a CSV?",
        default=False,
        style=STYLE,
    ).ask()
    if want_csv:
        path = questionary.text(
            "Nombre del archivo:",
            default="horario.csv",
            style=STYLE,
        ).ask()
        if path:
            export_csv(ranked, path, top_n=top_n)
