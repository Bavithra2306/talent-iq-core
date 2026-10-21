"""Job categories shared by JDs and resumes so the two always line up.

The list is only a hint to the LLM: any field of work is allowed. normalize_category()
folds common spellings into one name so "Healthcare", "Medical" and "medical field"
don't end up as different categories.
"""

import re

SUGGESTED_CATEGORIES = [
    "IT",
    "Medical",
    "Finance",
    "Engineering",
    "Education",
    "Sales & Marketing",
    "HR",
    "Legal",
    "Hospitality",
    "Manufacturing",
    "Design",
    "Operations",
    "Other",
]

# lower-case synonym -> canonical name
_SYNONYMS = {
    "it": "IT",
    "information technology": "IT",
    "software": "IT",
    "software development": "IT",
    "software engineering": "IT",
    "technology": "IT",
    "tech": "IT",
    "data science": "IT",
    "medical": "Medical",
    "healthcare": "Medical",
    "health care": "Medical",
    "medicine": "Medical",
    "nursing": "Medical",
    "pharmacy": "Medical",
    "finance": "Finance",
    "accounting": "Finance",
    "banking": "Finance",
    "engineering": "Engineering",
    "civil engineering": "Engineering",
    "mechanical engineering": "Engineering",
    "electrical engineering": "Engineering",
    "education": "Education",
    "teaching": "Education",
    "sales": "Sales & Marketing",
    "marketing": "Sales & Marketing",
    "sales & marketing": "Sales & Marketing",
    "sales and marketing": "Sales & Marketing",
    "hr": "HR",
    "human resources": "HR",
    "legal": "Legal",
    "law": "Legal",
    "hospitality": "Hospitality",
    "manufacturing": "Manufacturing",
    "design": "Design",
    "operations": "Operations",
    "other": "Other",
    # categories used by the public LiveCareer resume datasets (Kaggle / Hugging Face)
    "information-technology": "IT",
    "accountant": "Finance",
    "advocate": "Legal",
    "teacher": "Education",
    "chef": "Hospitality",
    "designer": "Design",
    "arts": "Design",
    "apparel": "Design",
    "digital-media": "Sales & Marketing",
    "public-relations": "Sales & Marketing",
    "business-development": "Sales & Marketing",
    "construction": "Engineering",
    "automobile": "Engineering",
    "aviation": "Engineering",
}


def normalize_category(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    cleaned = " ".join(value.split())
    key = cleaned.lower().replace(" field", "").replace(" industry", "").strip()
    if key in _SYNONYMS:
        return _SYNONYMS[key]
    if cleaned.isupper() and len(cleaned) <= 4:  # acronyms like BPO, QA stay as they are
        return cleaned
    return cleaned[:60].title()


# keyword word -> field of work. Checked in this order, so "software engineer" is IT
# even though "engineer" alone means Engineering.
_KEYWORD_WORDS = [
    ("IT", {"software", "developer", "programmer", "coder", "devops", "sre", "python", "java", "javascript",
            "typescript", "react", "angular", "node", "django", "flask", "sql", "aws", "cloud", "backend",
            "frontend", "fullstack", "full-stack", "data", "ml", "ai", "qa", "tester", "web", "android",
            "ios", "it", "tech", "technology", "programming", "c#", "c++", ".net", "golang"}),
    ("Medical", {"doctor", "nurse", "nursing", "physician", "pharmacist", "pharmacy", "medical", "healthcare",
                 "dentist", "surgeon", "clinical"}),
    ("Finance", {"accountant", "accounting", "finance", "financial", "auditor", "banker", "banking", "tax"}),
    ("Education", {"teacher", "teaching", "lecturer", "professor", "education", "tutor", "trainer"}),
    ("Sales & Marketing", {"sales", "marketing", "seo", "brand", "advertising"}),
    ("HR", {"hr", "recruiter", "recruitment", "payroll"}),
    ("Legal", {"lawyer", "advocate", "legal", "law", "paralegal"}),
    ("Hospitality", {"chef", "cook", "hotel", "hospitality", "waiter", "barista"}),
    ("Design", {"designer", "design", "ux", "ui", "graphic", "illustrator", "animator"}),
    ("Operations", {"operations", "logistics", "supply", "warehouse"}),
    ("Engineering", {"engineer", "mechanical", "civil", "electrical", "construction", "automobile", "aviation"}),
]


def guess_category(keyword: str | None) -> str | None:
    """Field of work for a search keyword such as "python developer" or "staff nurse"."""
    if not keyword or not keyword.strip():
        return None
    words = set(re.findall(r"[a-z0-9#+.\-]+", keyword.lower()))
    for category, vocabulary in _KEYWORD_WORDS:
        if words & vocabulary:
            return category
    return normalize_category(keyword) if keyword.strip().lower() in _SYNONYMS else None
