"""Turn one keyword (for example "software engineer" or "python developer") into what each
source understands.

* GitHub: a user search such as ``language:python type:user repos:>=3``.
* Stack Overflow: one tag such as ``python`` (top answerers of that tag).
* Hugging Face and Kaggle: a field of work such as ``IT`` (their resumes are grouped by field).

GitHub and Stack Overflow only hold developers, so they are skipped, with a note, when the
keyword is clearly about another field (for example "nurse").
"""

import re
from dataclasses import dataclass, field

from src.constants.categories import guess_category

# keyword word -> GitHub language
_GITHUB_LANGUAGES = {
    "python": "Python", "django": "Python", "flask": "Python", "fastapi": "Python",
    "java": "Java", "spring": "Java", "kotlin": "Kotlin",
    "javascript": "JavaScript", "js": "JavaScript", "react": "JavaScript", "reactjs": "JavaScript",
    "angular": "JavaScript", "vue": "JavaScript", "node": "JavaScript", "nodejs": "JavaScript", "node.js": "JavaScript",
    "typescript": "TypeScript", "go": "Go", "golang": "Go",
    "c#": "C#", "csharp": "C#", ".net": "C#", "dotnet": "C#",
    "c++": "C++", "cpp": "C++", "php": "PHP", "ruby": "Ruby", "swift": "Swift",
    "rust": "Rust", "scala": "Scala",
}

# keyword word -> Stack Overflow tag
_STACK_TAGS = {
    "python": "python", "django": "django", "flask": "flask", "fastapi": "fastapi",
    "java": "java", "spring": "spring-boot", "kotlin": "kotlin",
    "javascript": "javascript", "js": "javascript", "react": "reactjs", "reactjs": "reactjs",
    "angular": "angular", "vue": "vue.js", "node": "node.js", "nodejs": "node.js", "node.js": "node.js",
    "typescript": "typescript", "go": "go", "golang": "go",
    "c#": "c#", "csharp": "c#", ".net": ".net", "dotnet": ".net",
    "c++": "c++", "cpp": "c++", "php": "php", "ruby": "ruby", "swift": "swift",
    "rust": "rust", "scala": "scala", "sql": "sql", "docker": "docker", "kubernetes": "kubernetes",
    "aws": "amazon-web-services", "devops": "devops",
}

# Top answerers of this tag are experienced software engineers in general.
_GENERIC_STACK_TAG = "software-design"


@dataclass
class Resolved:
    category: str | None = None
    github_query: str | None = None
    stack_tag: str | None = None
    notes: dict[str, str] = field(default_factory=dict)  # source type -> note for the user


def _words(keyword: str) -> list[str]:
    return re.findall(r"[a-z0-9#+.]+", keyword.lower())


def resolve(keyword: str) -> Resolved:
    keyword = " ".join(keyword.split())
    words = [w.strip(".") if w not in {".net", "node.js", "vue.js"} else w for w in _words(keyword)]
    category = guess_category(keyword)
    out = Resolved(category=category)

    language = next((_GITHUB_LANGUAGES[w] for w in words if w in _GITHUB_LANGUAGES), None)
    tag = next((_STACK_TAGS[w] for w in words if w in _STACK_TAGS), None)
    is_it = category == "IT" or language is not None or tag is not None

    if language:
        out.github_query = f"language:{language} type:user repos:>=3 followers:>=5"
    elif is_it:
        out.github_query = "type:user repos:>=5 followers:>=5"
        out.notes["github"] = (f'"{keyword}" has no programming language in it, so active developers in '
                               "general are listed. Add one, like python or java, to narrow it down.")
    else:
        out.notes["github"] = f'Skipped: GitHub only has developers, and "{keyword}" is not a developer role.'

    if tag:
        out.stack_tag = tag
    elif is_it:
        out.stack_tag = _GENERIC_STACK_TAG
        out.notes["stackexchange"] = (f'"{keyword}" has no technology in it, so top software-design answerers '
                                      "are listed. Add one, like python or java, to narrow it down.")
    else:
        out.notes["stackexchange"] = f'Skipped: Stack Overflow only covers programming, and "{keyword}" is not a developer role.'

    if category is None:
        note = f'No field of work found for "{keyword}", so resumes from any field are used.'
        out.notes["huggingface"] = out.notes["kaggle"] = note
    return out
