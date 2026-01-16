import re
from typing import Dict, Any, List, Tuple
from rapidfuzz import fuzz
from app.models import StoryImpactRequest
from app.services.neo4j_service import Neo4jService

STOP = set([
    "the","a","an","and","or","to","of","in","for","on","with","as","is","are","be","by","from",
    "user","users","story","acceptance","criteria","should","must","when","then","after","before"
])

def tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", (text or "").lower())
    return [t for t in tokens if t not in STOP and len(t) > 2]

class StoryImpactService:
    """Scaffold: heuristically suggest change locations from a superimposed graph.

    This does NOT guarantee correctness; it's a ranked hint list to speed up analysis.
    You can later replace ranking with LLM/embeddings + graph traversal.
    """

    def __init__(self, neo: Neo4jService):
        self.neo = neo

    def suggest_change_locations(self, req: StoryImpactRequest) -> Dict[str, Any]:
        text = " ".join([req.story_title, req.description] + (req.acceptance_criteria or []))
        tokens = tokenize(text)

        # Pull candidate Types (classes) with key Spring annotations or common naming
        q = """MATCH (t:Type)
               WHERE exists(t.project_name) AND exists(t.repo_id)
                 AND (any(a IN coalesce(t.annotations,[]) WHERE a IN ['RestController','Controller','Service','Repository','Component'])
                      OR t.name =~ '.*(Controller|Service|Repository|Manager|Handler|Client|Config).*')
               RETURN t.project_name as project_name, t.repo_id as repo_id, t.fqn as fqn, t.name as name,
                      coalesce(t.annotations,[]) as annotations, t.file as file
               LIMIT 4000"""
        rows = self.neo.run(q)

        scored: List[Tuple[int, Dict[str, Any]]] = []
        for r in rows:
            name = r["name"] or ""
            fqn = r["fqn"] or ""
            ann = " ".join(r["annotations"] or [])
            hay = f"{name} {fqn} {ann}".lower()

            score = 0
            for t in tokens:
                if t in hay:
                    score += 12
                else:
                    # fuzzy match on class short name
                    score += int(fuzz.partial_ratio(t, name.lower()) > 85) * 6

            # Bonus if this class is CHANGED in the supergraph (if present)
            qd = """MATCH (d:DiffMarker {supergraph_id:$sid, fqn:$fqn})
                    RETURN d.status as status"""
            dr = self.neo.run(qd, {"sid": req.supergraph_id, "fqn": fqn})
            if dr:
                status = dr[0]["status"]
                if status == "CHANGED":
                    score += 10
                elif status in ("ADDED","REMOVED"):
                    score += 6

            if score > 0:
                scored.append((score, {
                    "project_name": r["project_name"],
                    "repo_id": r["repo_id"],
                    "fqn": fqn,
                    "name": name,
                    "annotations": r["annotations"],
                    "file": r["file"],
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: max(1, req.top_k)]

        return {
            "supergraph_id": req.supergraph_id,
            "tokens": tokens[:50],
            "candidates": [{"score": s, **d} for s, d in top],
            "notes": [
                "This is a heuristic starter. Best results when story uses domain terms that appear in class names/annotations.",
                "Next step: add endpoint mapping + service wiring edges, and use graph traversal for impact radius."
            ]
        }
