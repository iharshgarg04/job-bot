"""Referral finder — finds employees at target companies and generates referral request messages."""

import asyncio
import subprocess
import json
from dataclasses import dataclass, field, asdict
from profile import PROFILE


@dataclass
class ReferralContact:
    name: str
    title: str
    company: str
    linkedin_url: str
    connection_degree: str = ""  # "1st", "2nd", "3rd"
    mutual_connections: int = 0
    is_alumni: bool = False
    is_same_company: bool = False
    is_recruiter: bool = False
    message: str = ""
    status: str = "found"  # found, message_sent, responded, referred

    def to_dict(self):
        return asdict(self)


async def find_referrals(page, company: str, job_title: str, max_results: int = 5) -> list[ReferralContact]:
    """Search LinkedIn for employees at a company who could give referrals."""
    contacts = []

    # Strategy 1: Search for alumni at the company
    alumni_contacts = await _search_linkedin_people(
        page, company, extra_filter="Chitkara", priority="alumni"
    )
    contacts.extend(alumni_contacts)

    # Strategy 2: Search for people from Coding Ninjas who moved to that company
    cn_contacts = await _search_linkedin_people(
        page, company, extra_filter="Coding Ninjas", priority="ex-colleague"
    )
    contacts.extend(cn_contacts)

    # Strategy 3: Search for recruiters/HR at the company
    recruiter_contacts = await _search_linkedin_people(
        page, company, extra_filter="recruiter OR hiring OR talent", priority="recruiter"
    )
    contacts.extend(recruiter_contacts)

    # Strategy 4: General employees (engineers/developers)
    if len(contacts) < max_results:
        dev_contacts = await _search_linkedin_people(
            page, company, extra_filter="software engineer OR developer", priority="employee"
        )
        contacts.extend(dev_contacts)

    # Deduplicate by LinkedIn URL
    seen = set()
    unique = []
    for c in contacts:
        if c.linkedin_url not in seen:
            seen.add(c.linkedin_url)
            unique.append(c)

    # Sort: alumni first, then ex-colleagues, then recruiters, then employees
    priority_order = {"alumni": 0, "ex-colleague": 1, "recruiter": 2, "employee": 3}
    unique.sort(key=lambda c: (
        priority_order.get("alumni" if c.is_alumni else "ex-colleague" if c.is_same_company else "recruiter" if c.is_recruiter else "employee", 3),
        -c.mutual_connections,
    ))

    # Generate personalized messages for top contacts
    for contact in unique[:max_results]:
        contact.message = _generate_referral_message(contact, company, job_title)

    return unique[:max_results]


async def _search_linkedin_people(page, company: str, extra_filter: str = "", priority: str = "employee") -> list[ReferralContact]:
    """Search LinkedIn People for employees at a company."""
    contacts = []

    query = f"{company} {extra_filter}".strip()
    search_url = f"https://www.linkedin.com/search/results/people/?keywords={query.replace(' ', '%20')}&origin=GLOBAL_SEARCH_HEADER"

    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)

        # Extract people from search results
        people = await page.evaluate("""() => {
            const results = [];
            const seen = new Set();

            // Find all profile links
            const profileLinks = document.querySelectorAll('a[href*="/in/"]');
            for (const link of profileLinks) {
                const href = link.getAttribute('href').split('?')[0];
                if (seen.has(href)) continue;

                const nameEl = link.querySelector('span[aria-hidden="true"]');
                if (!nameEl) continue;

                const name = nameEl.textContent.trim();
                if (!name || name.length < 3 || name === 'LinkedIn Member') continue;

                seen.add(href);

                // Walk up to find the card container and get title/subtitle
                let title = '';
                let mutualCount = 0;
                let degree = '';
                let card = link.closest('li') || link.closest('div');
                if (card) {
                    const allText = card.textContent;
                    // Get subtitle (usually role + company)
                    const spans = card.querySelectorAll('div, p, span');
                    for (const s of spans) {
                        const t = s.textContent.trim();
                        if (t.length > 10 && t.length < 120 && t !== name &&
                            !t.includes('mutual') && !t.includes('Connect') &&
                            !t.includes('recent entity') && !t.includes('history') &&
                            !t.includes('Message') && !t.includes('Follow')) {
                            if (!title) title = t;
                        }
                    }
                    // Get mutual connections
                    const mutualMatch = allText.match(/(\\d+)\\s*mutual/);
                    if (mutualMatch) mutualCount = parseInt(mutualMatch[1]);
                    // Get degree
                    if (allText.includes('1st')) degree = '1st';
                    else if (allText.includes('2nd')) degree = '2nd';
                    else if (allText.includes('3rd')) degree = '3rd';
                }

                results.push({ name, title, url: href, degree, mutualCount });
            }
            return results;
        }""")

        for p in people[:5]:
            is_alumni = "chitkara" in p.get("title", "").lower() or priority == "alumni"
            is_same_co = "coding ninjas" in p.get("title", "").lower() or priority == "ex-colleague"
            is_recruiter = any(kw in p.get("title", "").lower() for kw in ["recruiter", "hiring", "talent", "hr ", "human resource"])

            contact = ReferralContact(
                name=p["name"],
                title=p.get("title", "")[:100],
                company=company,
                linkedin_url=f"https://www.linkedin.com{p['url']}" if p["url"].startswith("/") else p["url"],
                connection_degree=p.get("degree", ""),
                mutual_connections=p.get("mutualCount", 0),
                is_alumni=is_alumni,
                is_same_company=is_same_co,
                is_recruiter=is_recruiter,
            )
            contacts.append(contact)

    except Exception as e:
        print(f"  [referral] Error searching {company}: {e}")

    return contacts


def _generate_referral_message(contact: ReferralContact, company: str, job_title: str) -> str:
    """Use Claude to generate a personalized referral request message."""
    context = ""
    if contact.is_alumni:
        context = f"We're both from Chitkara University."
    elif contact.is_same_company:
        context = f"We both worked at Coding Ninjas."
    elif contact.is_recruiter:
        context = f"You're hiring at {company}."
    elif contact.mutual_connections > 0:
        context = f"We have {contact.mutual_connections} mutual connections."

    prompt = f"""Write a SHORT LinkedIn connection request message (under 200 characters) asking for a referral.

From: Harsh Garg, Software Developer at Coding Ninjas (1 year exp, Ruby on Rails, Angular, PostgreSQL, AWS)
To: {contact.name}, {contact.title}
Company: {company}
Job: {job_title}
Connection: {context}

Rules:
- Under 200 characters (LinkedIn limit for connection requests)
- Be genuine, not salesy
- Mention the specific role
- If alumni/ex-colleague, mention that connection
- Don't use "I hope this finds you well"

Return ONLY the message text, nothing else."""

    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            msg = result.stdout.strip().strip('"').strip("'")
            # Ensure under 200 chars
            if len(msg) > 200:
                msg = msg[:197] + "..."
            return msg
    except Exception:
        pass

    # Fallback message
    if contact.is_alumni:
        return f"Hi {contact.name.split()[0]}, fellow Chitkara alum here! I'm interested in the {job_title} role at {company}. Would you be open to referring me?"
    elif contact.is_same_company:
        return f"Hi {contact.name.split()[0]}, I'm at Coding Ninjas too! Interested in the {job_title} role at {company}. Could you refer me?"
    else:
        return f"Hi {contact.name.split()[0]}, I'm a Software Developer interested in the {job_title} role at {company}. Would love to connect!"
