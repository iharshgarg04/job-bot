"""Base scraper class for job boards."""

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    source: str  # naukri, indeed, linkedin, instahyre, wellfound
    description: str = ""
    salary: str = ""
    experience: str = ""
    skills: list[str] = field(default_factory=list)
    posted_date: str = ""
    match_score: float = 0.0
    match_reason: str = ""
    status: str = "scraped"  # scraped -> matched -> approved -> applied -> rejected

    def to_dict(self):
        return asdict(self)


class BaseScraper:
    """Base class for all job board scrapers."""

    name: str = "base"

    async def scrape(self, titles: list[str], locations: list[str], experience: int) -> list[Job]:
        """Scrape jobs from the platform. Override in subclass."""
        raise NotImplementedError

    def _clean_text(self, text: str) -> str:
        """Remove extra whitespace from text."""
        return " ".join(text.split()).strip()
