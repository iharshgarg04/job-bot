"""CLI review interface — review and approve matched jobs before applying."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from scrapers.base import Job
from tracker import log_application

console = Console()


def review_jobs(jobs: list[Job]) -> list[Job]:
    """Interactive CLI to review and approve/reject jobs."""
    if not jobs:
        console.print("[yellow]No matched jobs to review.[/yellow]")
        return []

    approved = []
    console.print(f"\n[bold cyan]Found {len(jobs)} matched jobs for review[/bold cyan]\n")

    # Show summary table first
    table = Table(title="Matched Jobs", show_lines=True)
    table.add_column("#", style="bold", width=4)
    table.add_column("Score", style="green", width=6)
    table.add_column("Title", style="cyan", width=30)
    table.add_column("Company", style="yellow", width=20)
    table.add_column("Location", width=15)
    table.add_column("Source", width=10)
    table.add_column("Salary", width=15)

    for i, job in enumerate(jobs, 1):
        table.add_row(
            str(i),
            f"{job.match_score:.0f}",
            job.title[:30],
            job.company[:20],
            job.location[:15],
            job.source,
            job.salary[:15] if job.salary else "-",
        )

    console.print(table)
    console.print()

    # Options
    console.print("[bold]Options:[/bold]")
    console.print("  [green]a[/green] = approve all    [yellow]r[/yellow] = review one by one    [red]s[/red] = skip all")
    console.print("  [cyan]1,3,5[/cyan] = approve specific jobs by number")
    console.print()

    choice = Prompt.ask("Choose", default="r")

    if choice.lower() == "a":
        for job in jobs:
            job.status = "approved"
            log_application(job, "approved")
            approved.append(job)
        console.print(f"[green]Approved all {len(approved)} jobs![/green]")

    elif choice.lower() == "s":
        console.print("[yellow]Skipped all jobs.[/yellow]")

    elif choice.lower() == "r":
        # Review one by one
        for i, job in enumerate(jobs, 1):
            console.print()
            panel_content = (
                f"[cyan]Title:[/cyan] {job.title}\n"
                f"[yellow]Company:[/yellow] {job.company}\n"
                f"[green]Location:[/green] {job.location}\n"
                f"[blue]Source:[/blue] {job.source}\n"
                f"[magenta]Salary:[/magenta] {job.salary or 'Not specified'}\n"
                f"[white]Experience:[/white] {job.experience or 'Not specified'}\n"
                f"[green]Match Score:[/green] {job.match_score:.0f}/100\n"
                f"[white]Reason:[/white] {job.match_reason}\n"
                f"[blue]URL:[/blue] {job.url}\n"
                f"\n[dim]{job.description[:300]}{'...' if len(job.description) > 300 else ''}[/dim]"
            )
            console.print(Panel(panel_content, title=f"Job {i}/{len(jobs)}", border_style="cyan"))

            if Confirm.ask("  Approve this job?", default=True):
                job.status = "approved"
                log_application(job, "approved")
                approved.append(job)
                console.print("  [green]Approved![/green]")
            else:
                job.status = "rejected"
                log_application(job, "rejected")
                console.print("  [red]Skipped.[/red]")

    else:
        # Parse comma-separated numbers
        try:
            nums = [int(n.strip()) for n in choice.split(",")]
            for n in nums:
                if 1 <= n <= len(jobs):
                    job = jobs[n - 1]
                    job.status = "approved"
                    log_application(job, "approved")
                    approved.append(job)
            console.print(f"[green]Approved {len(approved)} jobs![/green]")
        except ValueError:
            console.print("[red]Invalid input. No jobs approved.[/red]")

    return approved
