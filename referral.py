"""Referral helper — generates LinkedIn search links and personalized messages."""

import subprocess
from dataclasses import dataclass, asdict


@dataclass
class ReferralStrategy:
    label: str
    search_url: str
    message_template: str

    def to_dict(self):
        return asdict(self)


def get_referral_strategies(company: str, job_title: str) -> list[dict]:
    """Generate referral search links and messages for a company.

    Instead of scraping (unreliable), we give the user:
    1. Direct LinkedIn search URLs (open in browser, find people yourself)
    2. Claude-generated personalized messages ready to copy-paste
    """
    company_encoded = company.replace(" ", "%20")

    strategies = [
        {
            "label": "Chitkara Alumni at this company",
            "description": "Find fellow Chitkara University alumni who work here",
            "search_url": f"https://www.linkedin.com/search/results/people/?keywords={company_encoded}&schoolFilter=%5B%22Chitkara%20University%22%5D",
            "message": "",
            "priority": 1,
        },
        {
            "label": "Coding Ninjas colleagues who moved here",
            "description": "Find ex-Coding Ninjas people now at this company",
            "search_url": f"https://www.linkedin.com/search/results/people/?keywords={company_encoded}%20%22Coding%20Ninjas%22",
            "message": "",
            "priority": 2,
        },
        {
            "label": "Company employees page",
            "description": "Browse all employees — filter by Engineering",
            "search_url": f"https://www.linkedin.com/company/{company.lower().replace(' ', '-')}/people/",
            "message": "",
            "priority": 3,
        },
        {
            "label": "Engineers & Developers",
            "description": "Software engineers at this company",
            "search_url": f"https://www.linkedin.com/search/results/people/?keywords={company_encoded}%20software%20engineer&origin=GLOBAL_SEARCH_HEADER",
            "message": "",
            "priority": 4,
        },
        {
            "label": "Recruiters & HR",
            "description": "People who handle hiring here",
            "search_url": f"https://www.linkedin.com/search/results/people/?keywords={company_encoded}%20recruiter%20hiring&origin=GLOBAL_SEARCH_HEADER",
            "message": "",
            "priority": 5,
        },
    ]

    # Generate messages using Claude
    messages = _generate_messages(company, job_title)

    for i, strategy in enumerate(strategies):
        if i < len(messages):
            strategy["message"] = messages[i]

    return strategies


def _generate_messages(company: str, job_title: str) -> list[str]:
    """Use Claude to generate 5 referral messages for different scenarios."""
    prompt = f"""Generate 5 SHORT LinkedIn connection request messages (each UNDER 200 characters) for asking referrals.

Candidate: Harsh Garg, Software Developer at Coding Ninjas (1yr exp, Ruby on Rails, Angular, PostgreSQL, AWS)
Target: {job_title} role at {company}

Generate messages for these 5 scenarios:
1. To a college alumni (Chitkara University) at {company}
2. To an ex-Coding Ninjas colleague now at {company}
3. To a random employee at {company}
4. To a software engineer at {company}
5. To a recruiter/HR at {company}

Rules:
- Each message MUST be under 200 characters
- Be genuine and specific
- Mention the role
- For alumni/ex-colleague, reference the shared connection

Return ONLY a JSON array of 5 strings: ["msg1", "msg2", "msg3", "msg4", "msg5"]"""

    try:
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            import json
            text = result.stdout.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            messages = json.loads(text)
            # Trim to 200 chars
            return [m[:200] for m in messages]
    except Exception:
        pass

    # Fallback messages
    return [
        f"Hi! Fellow Chitkara alum here. I'm interested in the {job_title} role at {company}. Could you refer me?",
        f"Hi! I'm at Coding Ninjas and interested in the {job_title} role at {company}. Would you be open to a referral?",
        f"Hi! I'm a Software Developer interested in the {job_title} role at {company}. Would love to connect!",
        f"Hi! I'm a dev with Rails/Angular exp, interested in the {job_title} role at {company}. Happy to chat!",
        f"Hi! I saw the {job_title} opening at {company}. I'm a Software Developer with 1yr exp. Would love to discuss!",
    ]
