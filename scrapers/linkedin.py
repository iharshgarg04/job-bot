"""LinkedIn job scraper using public job search (no login required)."""

import httpx
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Job

# LinkedIn location GeoIDs for Indian cities
LOCATION_GEO_IDS = {
    "bangalore": "105214831",
    "bengaluru": "105214831",
    "gurugram": "115884833",
    "gurgaon": "115884833",
    "pune": "114806696",
    "hyderabad": "105556991",
    "remote": "",
}


class LinkedInScraper(BaseScraper):
    name = "linkedin"

    async def scrape(self, titles: list[str], locations: list[str], experience: int) -> list[Job]:
        jobs = []
        search_titles = titles[:3]

        for title in search_titles:
            for location in locations:
                new_jobs = await self._search(title, location)
                jobs.extend(new_jobs)

        seen = set()
        unique = []
        for job in jobs:
            if job.url not in seen:
                seen.add(job.url)
                unique.append(job)
        return unique

    async def _search(self, title: str, location: str) -> list[Job]:
        jobs = []
        geo_id = LOCATION_GEO_IDS.get(location.lower(), "")

        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        params = {
            "keywords": title,
            "location": f"{location}, India",
            "f_TPR": "r604800",  # Past week
            "f_E": "2",  # Entry level
            "start": 0,
            "count": 25,
        }
        if geo_id:
            params["geoId"] = geo_id

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html",
        }

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=headers)
                if resp.status_code != 200:
                    print(f"  [linkedin] HTTP {resp.status_code} for {title} in {location}")
                    return jobs

                soup = BeautifulSoup(resp.text, "lxml")
                cards = soup.select("li")

                for card in cards[:25]:
                    title_el = card.select_one("h3.base-search-card__title")
                    company_el = card.select_one("h4.base-search-card__subtitle a")
                    location_el = card.select_one("span.job-search-card__location")
                    link_el = card.select_one("a.base-card__full-link")
                    date_el = card.select_one("time")

                    if not title_el:
                        continue

                    job_url = link_el["href"].split("?")[0] if link_el and link_el.get("href") else ""

                    job = Job(
                        title=self._clean_text(title_el.get_text()),
                        company=self._clean_text(company_el.get_text()) if company_el else "",
                        location=self._clean_text(location_el.get_text()) if location_el else location,
                        url=job_url,
                        source="linkedin",
                        posted_date=date_el.get("datetime", "") if date_el else "",
                    )
                    jobs.append(job)
        except Exception as e:
            print(f"  [linkedin] Error searching {title} in {location}: {e}")

        return jobs
