import re
from typing import Any, Dict, List, Tuple

from rapidfuzz import fuzz

from app.models import IssueQueryRequest
from app.services.llm_service import LLMService
from app.services.neo4j_service import Neo4jService


STOP = set([
    "the","a","an","and","or","to","of","in","for","on","with","as","is","are","be","by","from",
    "user","users","story","acceptance","criteria","should","must","when","then","after","before",
    "proper","receive","receiving","correct","incorrect","missing","not","does","doesn","t","also"
])


def tokenize(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", (text or "").lower())
    return [t for t in tokens if t not in STOP and len(t) > 2]


def make_default_fulltext_query(tokens: List[str]) -> str:
    # Prefer OR list, limit to 18 terms.
    toks = tokens[:18]
    # Quote camelCase-like tokens less; here tokens already lower, so no quoting.
    return " OR ".join(toks)


class IssueQueryService:
    """Two-stage issue/story -> graph lookup.

    Stage 1: LLM-assisted fulltext query (if index exists, and score high enough).
    Stage 2: Fallback heuristic Cypher queries (LLM-assisted hints if available).
    """

    def __init__(self, neo: Neo4jService):
        self.neo = neo
        self.llm = LLMService()

    def query(self, req: IssueQueryRequest) -> Dict[str, Any]:
        issue_text = " ".join([req.title or "", req.description or ""] + (req.acceptance_criteria or []))
        base_tokens = tokenize(issue_text)

        # ----------------------
        # Stage 1: Fulltext
        # ----------------------
        ft = self.llm.extract_fulltext_query(issue_text)
        ft_query = (ft.get("query") if ft else None) or make_default_fulltext_query(base_tokens)

        ft_result = self._try_fulltext(req, ft_query)
        if ft_result is not None:
            return ft_result

        # ----------------------
        # Stage 2: Fallback Cypher
        # ----------------------
        hints = self.llm.extract_cypher_hints(issue_text)
        identifiers = []
        keywords = []
        llm_conf = 0.0
        if hints:
            identifiers = [s for s in (hints.get("identifiers") or []) if isinstance(s, str) and s.strip()]
            keywords = [s for s in (hints.get("keywords") or []) if isinstance(s, str) and s.strip()]
            llm_conf = float(hints.get("confidence") or 0.0)

        if not keywords:
            keywords = base_tokens[:25]

        candidates, confidence = self._fallback_cypher(req, identifiers, keywords)

        # Blend in LLM confidence lightly (won't exceed 1.0)
        blended = min(1.0, max(confidence, 0.0) * 0.85 + llm_conf * 0.15)

        return {
            "supergraph_id": req.supergraph_id,
            "stage_used": "fallback_cypher",
            "query_text": " ".join((identifiers + keywords)[:30]),
            "confidence": blended,
            "candidates": candidates,
            "debug": {
                "llm_enabled": self.llm.enabled(),
                "llm_hints": hints or None,
                "keyword_count": len(keywords),
            },
        }

    def _try_fulltext(self, req: IssueQueryRequest, ft_query: str) -> Any:
        """Returns response dict if stage accepted, else None."""
        # Check index exists (do not hard depend on it)
        try:
            idx_rows = self.neo.run("CALL db.indexes() YIELD name WHERE name = $n RETURN name", {"n": req.fulltext_index})
            if not idx_rows:
                return None
        except Exception:
            return None

        q = (
            "CALL db.index.fulltext.queryNodes($index, $q) "
            "YIELD node, score "
            "RETURN labels(node) AS labels, node AS node, score "
            "ORDER BY score DESC "
            "LIMIT $k"
        )
        rows = self.neo.run(q, {"index": req.fulltext_index, "q": ft_query, "k": int(req.top_k)})
        if not rows:
            return None

        top_score = float(rows[0]["score"] or 0.0)
        if top_score < float(req.fulltext_min_score or 0.0):
            return None

        # Normalize confidence based on top and spread.
        # - If there is only one hit, trust it moderately.
        # - If top is far above median, trust more.
        scores = [float(r["score"] or 0.0) for r in rows]
        median = sorted(scores)[len(scores)//2]
        if top_score <= 0:
            return None

        separation = (top_score - median) / max(top_score, 1e-9)
        conf = max(0.0, min(1.0, 0.55 + separation * 0.45))

        # Also require hitting the configured threshold
        if conf < float(req.fulltext_confidence_threshold or 0.0):
            return None

        candidates = []
        for r in rows:
            node = r["node"]
            labels = r["labels"] or []
            candidates.append({
                "labels": labels,
                "repo_id": node.get("repo_id"),
                "project_name": node.get("project_name"),
                "fqn": node.get("fqn") or node.get("owner_fqn"),
                "name": node.get("name"),
                "file": node.get("file"),
                "signature": node.get("signature"),
                "score": float(r["score"] or 0.0),
                "stage": "fulltext",
                "notes": [],
            })

        return {
            "supergraph_id": req.supergraph_id,
            "stage_used": "fulltext",
            "query_text": ft_query,
            "confidence": conf,
            "candidates": candidates,
            "debug": {
                "index": req.fulltext_index,
                "top_score": top_score,
                "median_score": median,
                "llm_enabled": self.llm.enabled(),
            },
        }

    def _fallback_cypher(self, req: IssueQueryRequest, identifiers: List[str], keywords: List[str]) -> Tuple[List[Dict[str, Any]], float]:
        """Heuristic Cypher search without fulltext."""
        # Strategy:
        # 1) Pull a bounded pool of Types/Methods/Fields.
        # 2) Locally score against identifiers/keywords.
        # 3) Bonus for having DiffMarker CHANGED under this supergraph.

        q = (
            "MATCH (n) "
            "WHERE (n:Type OR n:Method OR n:Field) "
            "  AND exists(n.repo_id) "
            "RETURN labels(n) AS labels, n AS node "
            "LIMIT 12000"
        )
        rows = self.neo.run(q)

        want = [w.strip() for w in (identifiers + keywords) if w and w.strip()]
        want_lower = [w.lower() for w in want]

        scored: List[Tuple[int, Dict[str, Any]]] = []

        for r in rows:
            node = r["node"]
            labels = r["labels"] or []
            name = (node.get("name") or "")
            fqn = (node.get("fqn") or node.get("owner_fqn") or "")
            signature = (node.get("signature") or "")
            file = (node.get("file") or "")
            hay = f"{name} {fqn} {signature} {file}".lower()

            score = 0
            for w in want_lower[:30]:
                if w in hay:
                    score += 10
                else:
                    # Fuzzy on short fields
                    score += (fuzz.partial_ratio(w, name.lower()) > 88) * 6

            # Diff bonus if possible (we store diff marker by fqn for Type/Method/Field in this project)
            # Try both direct fqn and owner_fqn where applicable.
            key_fqn = fqn or node.get("fqn") or ""
            if key_fqn:
                qd = "MATCH (d:DiffMarker {supergraph_id:$sid, fqn:$fqn}) RETURN d.status AS status LIMIT 1"
                dr = self.neo.run(qd, {"sid": req.supergraph_id, "fqn": key_fqn})
                if dr:
                    st = dr[0].get("status")
                    if st == "CHANGED":
                        score += 10
                    elif st in ("ADDED", "REMOVED"):
                        score += 6

            if score > 0:
                scored.append((score, {
                    "labels": labels,
                    "repo_id": node.get("repo_id"),
                    "project_name": node.get("project_name"),
                    "fqn": node.get("fqn") or node.get("owner_fqn"),
                    "name": name,
                    "file": file,
                    "signature": signature,
                    "score": float(score),
                    "stage": "fallback_cypher",
                    "notes": [],
                }))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: max(1, int(req.top_k))]

        # Confidence: normalize against max score and penalize flat lists.
        max_s = float(top[0][0]) if top else 0.0
        if max_s <= 0:
            return [], 0.0
        scores = [float(s) for s, _ in top]
        median = sorted(scores)[len(scores)//2]
        separation = (max_s - median) / max(max_s, 1e-9)
        conf = max(0.0, min(1.0, 0.45 + separation * 0.55))

        return [d for _, d in top], conf
