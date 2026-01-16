from fastapi import APIRouter
from app.models import StoryImpactRequest
from app.services.neo4j_service import Neo4jService
from app.services.superimpose_service import SuperimposeService
from app.services.story_impact_service import StoryImpactService

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
