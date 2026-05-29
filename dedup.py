"""Cross-source job deduplication."""

import re
from scrapers.base import Job


def _normalize(text: str) -> str:
    """Normalize text for comparison."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9 ]', '', text)
    text = re.sub(r'\s+', ' ', text)
    # Remove common suffixes
    for suffix in [" pvt ltd", " private limited", " ltd", " inc", " llp", " india",
                   " technologies", " solutions", " services", " software"]:
        text = text.replace(suffix, "")
    return text.strip()


def deduplicate_jobs(jobs: list[Job]) -> list[Job]:
    """Remove duplicate jobs across sources. Keep the one with most data."""
    seen = {}  # key -> best Job

    for job in jobs:
        # Create a dedup key from normalized company + title
        company_key = _normalize(job.company)
        title_key = _normalize(job.title)

        # Also handle slight title variations
        # "Software Engineer" vs "Software Engineer - I" vs "SDE 1"
        title_key = title_key.replace("sde 1", "software engineer")
        title_key = title_key.replace("sde1", "software engineer")
        title_key = title_key.replace("sde i", "software engineer")

        key = f"{company_key}|{title_key}"

        if key in seen:
            existing = seen[key]
            # Keep the one with more data (description, skills, salary)
            existing_richness = len(existing.description) + len(existing.skills) * 10 + (10 if existing.salary else 0)
            new_richness = len(job.description) + len(job.skills) * 10 + (10 if job.salary else 0)
            if new_richness > existing_richness:
                # Keep sources info
                job.match_reason = f"Also on: {existing.source}"
                seen[key] = job
        else:
            seen[key] = job

    result = list(seen.values())
    return result
