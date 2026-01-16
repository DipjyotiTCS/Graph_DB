from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl

class LocalIngestRequest(BaseModel):
    path: str = Field(..., description="Absolute/relative directory path that contains the extracted Java project")
    repo_id: str = Field(..., description="Short id to tag nodes (e.g., sb2, sb3)")
    project_name: str = Field("java-project", description="Logical project name tag")
    overwrite_repo: bool = True



class LocalSuperimposeRequest(BaseModel):
    left_path: str = Field(..., description="Path to left Java Spring Boot project root folder")
    right_path: str = Field(..., description="Path to right Java Spring Boot project root folder")
    left_repo_id: str = Field(..., description="Repo tag for left side (e.g., sb2)")
    right_repo_id: str = Field(..., description="Repo tag for right side (e.g., sb3)")
    supergraph_id: str = Field(..., description="Identifier for this comparison (e.g., sb2_vs_sb3)")
    project_name: str = Field("java-project", description="Logical project name tag")
    overwrite_repos: bool = True
    overwrite_supergraph: bool = True

class GitRepoSpec(BaseModel):
    repo_url: str
    branch: str = "main"
    token: Optional[str] = None
    repo_id: str

class GitSuperimposeRequest(BaseModel):
    left: GitRepoSpec
    right: GitRepoSpec
    supergraph_id: str = Field(..., description="Identifier for this comparison (e.g., sb2_vs_sb3)")
    project_name: str = "java-project"
    overwrite_repos: bool = True
    overwrite_supergraph: bool = True

class StoryImpactRequest(BaseModel):
    supergraph_id: str
    story_title: str
    description: str
    acceptance_criteria: List[str] = []
    top_k: int = 15


class IssueQueryRequest(BaseModel):
    """User-facing input for issue/story -> graph query."""

    supergraph_id: str
    title: str = ""
    description: str
    acceptance_criteria: List[str] = []

    # Retrieval controls
    top_k: int = 15

    # Fulltext stage (preferred)
    fulltext_index: str = "codeSearch"
    fulltext_confidence_threshold: float = 0.65
    fulltext_min_score: float = 0.0

    # Fallback stage (plain Cypher heuristics)
    fallback_confidence_threshold: float = 0.45


class IssueCandidate(BaseModel):
    labels: List[str]
    repo_id: Optional[str] = None
    project_name: Optional[str] = None
    fqn: Optional[str] = None
    name: Optional[str] = None
    file: Optional[str] = None
    signature: Optional[str] = None
    score: float = 0.0
    stage: str = ""
    notes: List[str] = []


class IssueQueryResponse(BaseModel):
    supergraph_id: str
    stage_used: str
    query_text: str
    confidence: float
    candidates: List[IssueCandidate]
    debug: dict = {}
