import os
import shutil
import tempfile

from fastapi import APIRouter, HTTPException
from app.models import LocalIngestRequest, GitSuperimposeRequest, LocalSuperimposeRequest
from app.services.java_parser import JavaProjectParser
from app.services.neo4j_service import Neo4jService
from app.services.graph_builder import GraphBuilder
from app.services.git_service import GitService
from app.services.superimpose_service import SuperimposeService
from app.settings import settings

router = APIRouter(prefix="/ingest", tags=["ingest"])

@router.post("/local")
def ingest_local(req: LocalIngestRequest):
    if not os.path.exists(req.path) or not os.path.isdir(req.path):
        raise HTTPException(status_code=400, detail=f"Path not found or not a directory: {req.path}")

    neo = Neo4jService()
    try:
        builder = GraphBuilder(neo)

        if req.overwrite_repo:
            neo.delete_repo(req.project_name, req.repo_id)

        parser = JavaProjectParser()
        graph = parser.parse_directory(req.path, project_name=req.project_name, repo_id=req.repo_id)
        builder.upsert_repo_graph(graph)

        return {
            "project_name": req.project_name,
            "repo_id": req.repo_id,
            "stats": {
                "parsed": graph.get("stats", {}),
                "neo4j": neo.repo_stats(req.project_name, req.repo_id),
            }
        }
    finally:
        neo.close()

@router.post("/local-superimpose")
def ingest_local_superimpose(req: LocalSuperimposeRequest):
    if not os.path.exists(req.left_path) or not os.path.isdir(req.left_path):
        raise HTTPException(status_code=400, detail=f"Left path not found or not a directory: {req.left_path}")
    if not os.path.exists(req.right_path) or not os.path.isdir(req.right_path):
        raise HTTPException(status_code=400, detail=f"Right path not found or not a directory: {req.right_path}")

    neo = Neo4jService()
    try:
        builder = GraphBuilder(neo)
        superimposer = SuperimposeService(neo)

        if req.overwrite_repos:
            neo.delete_repo(req.project_name, req.left_repo_id)
            neo.delete_repo(req.project_name, req.right_repo_id)

        if req.overwrite_supergraph:
            superimposer.delete_supergraph(req.supergraph_id)

        parser = JavaProjectParser()
        left_graph = parser.parse_directory(req.left_path, project_name=req.project_name, repo_id=req.left_repo_id)
        right_graph = parser.parse_directory(req.right_path, project_name=req.project_name, repo_id=req.right_repo_id)

        builder.upsert_repo_graph(left_graph)
        builder.upsert_repo_graph(right_graph)

        diff_summary = superimposer.superimpose_and_diff(
            project_name=req.project_name,
            left_repo_id=req.left_repo_id,
            right_repo_id=req.right_repo_id,
            supergraph_id=req.supergraph_id,
            left_root=req.left_path,
            right_root=req.right_path,
        )

        out_stats = {
            "left": {"parsed": left_graph.get("stats", {}), "neo4j": neo.repo_stats(req.project_name, req.left_repo_id)},
            "right": {"parsed": right_graph.get("stats", {}), "neo4j": neo.repo_stats(req.project_name, req.right_repo_id)},
            "supergraph": diff_summary
        }

        return {"project_name": req.project_name, "supergraph_id": req.supergraph_id, "stats": out_stats}
    finally:
        neo.close()

@router.post("/git-superimpose")
def ingest_git_superimpose(req: GitSuperimposeRequest):
    os.makedirs(settings.workdir, exist_ok=True)
    tmp_dir = tempfile.mkdtemp(prefix="superimpose_", dir=settings.workdir)

    git = GitService(tmp_dir)
    left_dir = None
    right_dir = None

    try:
        left_dir = git.clone(req.left.repo_url, req.left.branch, req.left.token, name=f"left_{req.left.repo_id}")
        right_dir = git.clone(req.right.repo_url, req.right.branch, req.right.token, name=f"right_{req.right.repo_id}")

        parser = JavaProjectParser()
        left_graph = parser.parse_directory(left_dir, project_name=req.project_name, repo_id=req.left.repo_id)
        right_graph = parser.parse_directory(right_dir, project_name=req.project_name, repo_id=req.right.repo_id)

        neo = Neo4jService()
        try:
            builder = GraphBuilder(neo)
            superimposer = SuperimposeService(neo)

            if req.overwrite_repos:
                neo.delete_repo(req.project_name, req.left.repo_id)
                neo.delete_repo(req.project_name, req.right.repo_id)

            if req.overwrite_supergraph:
                superimposer.delete_supergraph(req.supergraph_id)

            builder.upsert_repo_graph(left_graph)
            builder.upsert_repo_graph(right_graph)

            diff_summary = superimposer.superimpose_and_diff(
                project_name=req.project_name,
                left_repo_id=req.left.repo_id,
                right_repo_id=req.right.repo_id,
                supergraph_id=req.supergraph_id,
                left_root=left_dir,
                right_root=right_dir,
            )

            out_stats = {
                "left": {"parsed": left_graph.get("stats", {}), "neo4j": neo.repo_stats(req.project_name, req.left.repo_id)},
                "right": {"parsed": right_graph.get("stats", {}), "neo4j": neo.repo_stats(req.project_name, req.right.repo_id)},
                "supergraph": diff_summary
            }
        finally:
            neo.close()

        return {"project_name": req.project_name, "supergraph_id": req.supergraph_id, "stats": out_stats}

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
