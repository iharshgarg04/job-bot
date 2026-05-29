#!/usr/bin/env python3
"""Test matching on scraped jobs."""
import asyncio
from config import TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS
from scrapers import ALL_SCRAPERS
from matcher import keyword_score, match_jobs_keyword

async def test():
    all_jobs = []
    for ScraperClass in ALL_SCRAPERS:
        scraper = ScraperClass()
        print(f"Scraping {scraper.name}...")
        try:
            jobs = await scraper.scrape(TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS)
            all_jobs.extend(jobs)
            print(f"  Got {len(jobs)} jobs")
        except Exception as e:
            print(f"  Error: {e}")

    print(f"\nTotal scraped: {len(all_jobs)}")
    print("\nScoring jobs...")

    matched = match_jobs_keyword(all_jobs)
    print(f"Matched: {len(matched)} jobs\n")

    for job in matched[:15]:
        print(f"  [{job.match_score:5.0f}] {job.title} @ {job.company} ({job.location})")

asyncio.run(test())
