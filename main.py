#!/usr/bin/env python3
"""
Job Bot — Automated job finder & applicator
Usage:
    python main.py search     # Scrape + match + review jobs
    python main.py apply      # Apply to previously approved jobs
    python main.py review     # Re-review matched jobs
    python main.py status     # Show application stats
"""

import asyncio
import sys
from rich.console import Console
from rich.table import Table

from config import TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS
from scrapers import ALL_SCRAPERS
from scrapers.base import Job
from matcher import match_jobs_with_claude
from reviewer import review_jobs
from tracker import save_matched_jobs, load_matched_jobs, get_applied_urls
from applier import auto_apply
from dedup import deduplicate_jobs

console = Console()


async def search():
    """Full pipeline: scrape -> match -> review."""
    console.print("\n[bold cyan]Job Bot — Search & Match[/bold cyan]\n")

    # Step 1: Scrape all platforms
    all_jobs: list[Job] = []
    applied_urls = get_applied_urls()

    for ScraperClass in ALL_SCRAPERS:
        scraper = ScraperClass()
        console.print(f"[cyan]Scraping {scraper.name}...[/cyan]")
        try:
            jobs = await scraper.scrape(TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS)
            # Filter out already applied
            jobs = [j for j in jobs if j.url not in applied_urls and j.url]
            console.print(f"  [green]Found {len(jobs)} jobs from {scraper.name}[/green]")
            all_jobs.extend(jobs)
        except Exception as e:
            console.print(f"  [red]Error with {scraper.name}: {e}[/red]")

    if not all_jobs:
        console.print("[yellow]No jobs found. Try adjusting search criteria.[/yellow]")
        return

    console.print(f"\n[bold]Total jobs scraped: {len(all_jobs)}[/bold]")

    # Deduplicate across sources
    before = len(all_jobs)
    all_jobs = deduplicate_jobs(all_jobs)
    if before != len(all_jobs):
        console.print(f"[dim]Removed {before - len(all_jobs)} duplicates across sources[/dim]")

    # Step 2: Match with Claude
    console.print("\n[cyan]Matching jobs against your profile...[/cyan]")
    matched = await match_jobs_with_claude(all_jobs)
    console.print(f"[green]Matched {len(matched)} relevant jobs[/green]")

    if not matched:
        console.print("[yellow]No strong matches found. Try broadening search criteria.[/yellow]")
        return

    # Save matched jobs
    save_matched_jobs(matched)

    # Step 3: Review
    approved = review_jobs(matched)

    if approved:
        console.print(f"\n[bold green]{len(approved)} jobs approved![/bold green]")
        apply_now = input("Apply now? (y/n): ").strip().lower()
        if apply_now == "y":
            await auto_apply(approved)
    else:
        console.print("[yellow]No jobs approved.[/yellow]")


async def apply_saved():
    """Apply to previously approved jobs."""
    jobs = load_matched_jobs()
    approved = [j for j in jobs if j.status == "approved"]
    if not approved:
        console.print("[yellow]No approved jobs found. Run 'search' first.[/yellow]")
        return
    console.print(f"[cyan]Found {len(approved)} approved jobs to apply to.[/cyan]")
    await auto_apply(approved)


def review_saved():
    """Re-review matched jobs."""
    jobs = load_matched_jobs()
    if not jobs:
        console.print("[yellow]No matched jobs found. Run 'search' first.[/yellow]")
        return
    approved = review_jobs(jobs)
    if approved:
        save_matched_jobs(jobs)  # Update statuses


def show_status():
    """Show application statistics."""
    import csv
    import os
    from config import APPLICATIONS_LOG

    if not os.path.exists(APPLICATIONS_LOG):
        console.print("[yellow]No applications logged yet.[/yellow]")
        return

    stats = {"approved": 0, "applied": 0, "rejected": 0, "failed": 0, "error": 0}
    sources = {}

    with open(APPLICATIONS_LOG) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    for row in rows:
        status = row.get("status", "")
        source = row.get("source", "")
        stats[status] = stats.get(status, 0) + 1
        sources[source] = sources.get(source, 0) + 1

    # Summary
    console.print("\n[bold cyan]Application Stats[/bold cyan]\n")

    table = Table()
    table.add_column("Status", style="cyan")
    table.add_column("Count", style="green")
    for status, count in stats.items():
        if count > 0:
            table.add_row(status.capitalize(), str(count))
    console.print(table)

    # By source
    console.print()
    table2 = Table(title="By Platform")
    table2.add_column("Platform", style="cyan")
    table2.add_column("Count", style="green")
    for source, count in sources.items():
        table2.add_row(source, str(count))
    console.print(table2)

    # Recent applications
    console.print(f"\nTotal logged: {len(rows)}")


def main():
    if len(sys.argv) < 2:
        console.print(__doc__)
        console.print("[bold]Quick start:[/bold] python main.py search")
        return

    command = sys.argv[1].lower()

    if command == "search":
        asyncio.run(search())
    elif command == "apply":
        asyncio.run(apply_saved())
    elif command == "review":
        review_saved()
    elif command == "status":
        show_status()
    else:
        console.print(f"[red]Unknown command: {command}[/red]")
        console.print(__doc__)


if __name__ == "__main__":
    main()
