from typing import Annotated, Literal

from pydantic import BaseModel, Field, HttpUrl


class GitHubSource(BaseModel):
    type: Literal["github"] = "github"
    query: str | None = Field(default=None, min_length=2, max_length=200,
                              description='GitHub user search, e.g. "language:python location:chennai"; '
                                          "ignored when the run has a keyword")
    max_results: int = Field(default=10, ge=1, le=200)


class StackExchangeSource(BaseModel):
    type: Literal["stackexchange"] = "stackexchange"
    tag: str | None = Field(default=None, min_length=1, max_length=40,
                            description='e.g. "python", "reactjs"; ignored when the run has a keyword')
    site: str = Field(default="stackoverflow", max_length=40)
    max_results: int = Field(default=10, ge=1, le=200)


class HuggingFaceSource(BaseModel):
    type: Literal["huggingface"] = "huggingface"
    dataset: Literal["opensporks/resumes", "ahmedheakl/resume-atlas"] = "opensporks/resumes"
    category: str | None = Field(default=None, description="e.g. IT, Medical, Finance")
    max_results: int = Field(default=20, ge=1, le=200)


class KaggleSource(BaseModel):
    type: Literal["kaggle"] = "kaggle"
    category: str | None = None
    max_results: int = Field(default=20, ge=1, le=200)


class UrlSource(BaseModel):
    type: Literal["url"] = "url"
    urls: list[HttpUrl] = Field(min_length=1, max_length=200)
    follow_links: bool = Field(default=True, description="follow resume/CV file links on a page")


SourceSpec = Annotated[
    GitHubSource | StackExchangeSource | HuggingFaceSource | KaggleSource | UrlSource,
    Field(discriminator="type"),
]


class SourcingRunRequest(BaseModel):
    keyword: str | None = Field(
        default=None, min_length=2, max_length=100,
        description='e.g. "software engineer" or "python developer". When set, it picks the search '
                    "for GitHub, Stack Overflow, Hugging Face and Kaggle; their own fields are ignored.",
    )
    sources: list[SourceSpec] = Field(min_length=1, max_length=10)


class SourcingRunAccepted(BaseModel):
    batch_id: str


class SourceInfo(BaseModel):
    type: str
    name: str
    description: str
    parameters: dict
    configured: bool
    needs: str | None = None
    limits: str
    terms: str
    example: dict
