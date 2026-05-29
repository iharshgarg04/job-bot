"""Application tracker — logs all jobs to CSV and JSON."""

import csv
import json
import os
from datetime import datetime
from scrapers.base import Job
from config import APPLICATIONS_LOG, MATCHED_JOBS_LOG


def save_matched_jobs(jobs: list[Job]):
    """Save matched jobs to JSON for review."""
    data = [job.to_dict() for job in jobs]
    with open(MATCHED_JOBS_LOG, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved {len(jobs)} matched jobs to {MATCHED_JOBS_LOG}")


def load_matched_jobs() -> list[Job]:
    """Load matched jobs from JSON."""
    if not os.path.exists(MATCHED_JOBS_LOG):
        return []
    with open(MATCHED_JOBS_LOG) as f:
        data = json.load(f)
    return [Job(**item) for item in data]


def log_application(job: Job, status: str):
    """Log an application to CSV."""
    file_exists = os.path.exists(APPLICATIONS_LOG)

    with open(APPLICATIONS_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp", "title", "company", "location", "source",
                "url", "match_score", "status",
            ])
        writer.writerow([
            datetime.now().isoformat(),
            job.title,
            job.company,
            job.location,
            job.source,
            job.url,
            job.match_score,
            status,
        ])


def get_applied_urls() -> set[str]:
    """Get URLs of already-applied jobs to avoid duplicates."""
    if not os.path.exists(APPLICATIONS_LOG):
        return set()
    urls = set()
    with open(APPLICATIONS_LOG) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("status") in ("applied", "approved"):
                urls.add(row.get("url", ""))
    return urls
