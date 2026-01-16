from fastapi import APIRouter
from app.models import StoryImpactRequest, IssueQueryRequest
from app.services.neo4j_service import Neo4jService
from app.services.superimpose_service import SuperimposeService
from app.services.story_impact_service import StoryImpactService
from app.services.issue_query_service import IssueQueryService

router = APIRouter(prefix="/analysis", tags=["analysis"])

@router.get("/diff/{supergraph_id}")
def get_diff(supergraph_id: str):
    neo = Neo4jService()
    try:
        svc = SuperimposeService(neo)
        return svc.diff_summary(supergraph_id)
    finally:
        neo.close()

@router.post("/story-impact")
def story_impact(req: StoryImpactRequest):
    neo = Neo4jService()
    try:
        svc = StoryImpactService(neo)
        return svc.suggest_change_locations(req)
    finally:
        neo.close()


@router.post("/issue-query")
def issue_query(req: IssueQueryRequest):
    """Convert defect/user story text into a graph query and return ranked nodes.

    Flow:
    1) Use LLM (if configured) to generate a Neo4j fulltext query.
       - Run fulltext search and compute confidence from score separation.
       - If confidence >= threshold, return results.
    2) Otherwise fall back to non-fulltext Cypher heuristics (optionally LLM-assisted).
    """
    neo = Neo4jService()
    try:
        svc = IssueQueryService(neo)
        return svc.query(req)
    finally:
        neo.close()


