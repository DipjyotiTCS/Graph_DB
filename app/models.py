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
