#!/usr/bin/env python3
"""
Uniandes Schedule Optimizer
Usage examples:
  python3 main.py --courses ISIS-1221 MATE-1203 --term 202620
  python3 main.py --courses "INTRODUCCIÓN A LA PROGRAMACIÓN" MATE-1203 --rank min-gaps
  python3 main.py --nrc 75548 80985 --rank free-days --top 3
  python3 main.py --courses ISIS-1221 MATE-1203 --csv horario.csv
  python3 main.py --list-terms
"""
import argparse
import sys
from typing import Dict, List

import requests

from scraper import fetch_sections, fetch_course_bundle, get_terms, DEFAULT_TERM
from models import Section
from optimizer import find_valid_combinations
from ranker import rank_combinations
from display import print_summary, export_csv, console


RANK_CHOICES = ["free-days", "min-gaps", "no-early", "balanced"]


def resolve_courses(
    queries: List[str],
    term: str,
    session: requests.Session,
) -> Dict[str, List[Section]]:
    """
    For each query string, fetch matching sections.
    Groups sections by (prefix, course_num) so multi-section courses stay together.
    Returns dict: display_label -> [Section, ...]
    """
    course_groups: Dict[str, List[Section]] = {}

    for query in queries:
        try:
            with console.status(f"Buscando '{query}'…"):
                bundles = fetch_course_bundle(query, term=term, session=session)
        except RuntimeError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        if not bundles:
            console.print(f"[yellow]⚠ Sin resultados para '{query}'.[/yellow]")
            _stop_condition_no_sections(query)
            continue

        # Exact match if query looks like a course code
        import re as _re
        query_upper = query.upper().replace(" ", "-")
        is_code = bool(_re.match(r"^[A-Z]{2,6}-[A-Z0-9]+$", query_upper))
        if is_code and query_upper in bundles:
            bundles = {query_upper: bundles[query_upper]}

        if len(bundles) == 1:
            code, bundle = next(iter(bundles.items()))
            options = bundle.all_section_options()
            label = f"{code} — {bundle.title}"
            note = " [dim](incluye opciones magistral+complementaria)[/dim]" if bundle.has_complementary else ""
            console.print(f"  [green]✓[/green] {len(options)} opción(es) en [bold]{code}[/bold]{note}")
            course_groups[label] = options
        else:
            console.print(f"\n[yellow]⚠ '{query}' coincide con {len(bundles)} cursos:[/yellow]")
            for i, (code, bundle) in enumerate(sorted(bundles.items()), 1):
                console.print(f"    {i:2d}. [cyan]{code}[/cyan] — {bundle.title} ({len(bundle.magistral_sections)} secciones)")
            console.print(
                f"\n[dim]Sea más específico: --courses {list(bundles.keys())[0]}[/dim]"
            )
            sys.exit(1)

    return course_groups


def resolve_nrcs(
    nrcs: List[str],
    term: str,
    session: requests.Session,
) -> Dict[str, List[Section]]:
    """Fetch sections by NRC. Each NRC should match exactly one section."""
    course_groups: Dict[str, List[Section]] = {}

    for nrc in nrcs:
        try:
            with console.status(f"Buscando NRC {nrc}…"):
                sections = fetch_sections(nrc, term=term, session=session)
        except RuntimeError as e:
            console.print(f"[red]Error:[/red] {e}")
            sys.exit(1)

        exact = [s for s in sections if s.nrc == nrc]
        if not exact:
            console.print(f"[yellow]⚠ NRC {nrc} no encontrado para el término {term}.[/yellow]")
            _stop_condition_no_sections(nrc)
            continue

        sec = exact[0]
        label = f"NRC:{nrc} {sec.course_code} sec.{sec.section_num}"
        console.print(f"  [green]✓[/green] [bold]{sec.course_code}[/bold] — {sec.title} (sec.{sec.section_num})")
        course_groups[label] = [sec]

    return course_groups


def _stop_condition_no_sections(query: str) -> None:
    console.print(
        f"\n[bold red]⛔ '{query}' no tiene secciones disponibles.[/bold red]\n"
        "   • Verifica el código en [link]https://ofertadecursos.uniandes.edu.co[/link]\n"
        "   • Cambia el término con [bold]--term[/bold]\n"
    )
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optimizador de horarios — Universidad de los Andes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--courses", "-c",
        nargs="+",
        metavar="COURSE",
        help="Códigos de materia (p.ej. ISIS-1221 MATE-1203) o fragmento del nombre",
    )
    input_group.add_argument(
        "--nrc", "-n",
        nargs="+",
        metavar="NRC",
        help="NRCs exactos de secciones específicas",
    )

    parser.add_argument(
        "--term", "-t",
        default=DEFAULT_TERM,
        help=f"Período académico (default: {DEFAULT_TERM}). Use --list-terms para ver opciones.",
    )
    parser.add_argument(
        "--rank", "-r",
        default="free-days",
        choices=RANK_CHOICES,
        help="Criterio de ranking: free-days | min-gaps | no-early | balanced (default: free-days)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="Cuántas combinaciones mostrar (default: 5)",
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        help="Exportar resultados a CSV",
    )
    parser.add_argument(
        "--max-combos",
        type=int,
        default=10000,
        help="Límite máximo de combinaciones a evaluar (default: 10000)",
    )
    parser.add_argument(
        "--list-terms",
        action="store_true",
        help="Listar períodos académicos disponibles y salir",
    )

    args = parser.parse_args()

    session = requests.Session()

    if args.list_terms:
        console.print("\n[bold]Períodos disponibles:[/bold]")
        for t in get_terms(session):
            console.print(f"  [cyan]{t['term_desc']}[/cyan]  —  {t['term_name']}")
        return

    if not args.courses and not args.nrc:
        # No arguments → launch interactive mode
        from interactive import run_interactive
        run_interactive()
        return

    console.print(f"\n[bold]Buscando secciones para el período [cyan]{args.term}[/cyan]...[/bold]\n")

    if args.courses:
        course_groups = resolve_courses(args.courses, args.term, session)
    else:
        course_groups = resolve_nrcs(args.nrc, args.term, session)

    if not course_groups:
        console.print("[yellow]⚠ No se encontraron cursos válidos.[/yellow]")
        sys.exit(1)

    if len(course_groups) == 1 and not args.nrc:
        console.print("\n[dim]ℹ Solo un curso — todas sus secciones son válidas entre sí.[/dim]")

    console.print(f"\n[dim]Generando combinaciones (criterio: {args.rank})…[/dim]")

    combos = find_valid_combinations(course_groups, max_combinations=args.max_combos)

    if not combos:
        console.print("\n[red]❌ No hay combinaciones válidas (todos los horarios se cruzan).[/red]")
        console.print("[dim]Verifica que los cursos no se traslapen completamente.[/dim]")
        sys.exit(0)

    ranked = rank_combinations(combos, criterion=args.rank)

    print_summary(ranked, top_n=args.top, criterion=args.rank)

    if args.csv:
        export_csv(ranked, args.csv, top_n=args.top)


if __name__ == "__main__":
    main()
