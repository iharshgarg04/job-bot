#!/usr/bin/env python3
"""Quick test — scrape all platforms and show results count."""

import asyncio
from config import TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS
from scrapers import ALL_SCRAPERS
from dedup import deduplicate_jobs


async def test():
    total = 0
    all_jobs = []
    for ScraperClass in ALL_SCRAPERS:
        scraper = ScraperClass()
        print(f"Scraping {scraper.name}...")
        try:
            jobs = await scraper.scrape(TARGET_TITLES, TARGET_LOCATIONS, EXPERIENCE_YEARS)
            print(f"  Found {len(jobs)} jobs")
            for job in jobs[:3]:
                print(f"    - {job.title} @ {job.company} ({job.location})")
            total += len(jobs)
            all_jobs.extend(jobs)
        except Exception as e:
            print(f"  Error: {e}")

    print(f"\nTotal before dedup: {total}")
    deduped = deduplicate_jobs(all_jobs)
    print(f"Total after dedup: {len(deduped)}")
    print(f"Duplicates removed: {total - len(deduped)}")


asyncio.run(test())
