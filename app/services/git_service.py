import os
import shutil
from typing import Optional
from git import Repo

class GitService:
    def __init__(self, workdir: str):
        self.workdir = workdir

    def clone(self, repo_url: str, branch: str, token: Optional[str], name: str) -> str:
        target = os.path.join(self.workdir, name)
        if os.path.exists(target):
            shutil.rmtree(target, ignore_errors=True)

        url = repo_url
        if token and repo_url.startswith("https://"):
            url = repo_url.replace("https://", f"https://{token}@")

        Repo.clone_from(url, target, branch=branch, depth=1)
        return target
