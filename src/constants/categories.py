"""Job categories shared by JDs and resumes so the two always line up.

The list is only a hint to the LLM: any field of work is allowed. normalize_category()
folds common spellings into one name so "Healthcare", "Medical" and "medical field"
don't end up as different categories.
"""

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
    "banking": "Finance",
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
