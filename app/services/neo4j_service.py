from typing import Dict, Any, Optional, List
from neo4j import GraphDatabase
from app.settings import settings

class Neo4jService:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_username, settings.neo4j_password),
        )

    def close(self):
        if self.driver:
            self.driver.close()

    def run(self, cypher: str, params: Optional[Dict[str, Any]] = None):
        params = params or {}
        with self.driver.session(database=settings.neo4j_database) as session:
            return list(session.run(cypher, params))

    def ensure_constraints(self):
        stmts: List[str] = [
            "CREATE CONSTRAINT project_key IF NOT EXISTS FOR (pr:Project) REQUIRE (pr.project_name, pr.repo_id) IS UNIQUE",
            "CREATE CONSTRAINT pkg_key IF NOT EXISTS FOR (p:Package) REQUIRE (p.project_name, p.repo_id, p.fqn) IS UNIQUE",
            "CREATE CONSTRAINT type_key IF NOT EXISTS FOR (t:Type) REQUIRE (t.project_name, t.repo_id, t.fqn) IS UNIQUE",
            "CREATE CONSTRAINT method_key IF NOT EXISTS FOR (m:Method) REQUIRE (m.project_name, m.repo_id, m.owner_fqn, m.signature) IS UNIQUE",
            "CREATE CONSTRAINT field_key IF NOT EXISTS FOR (f:Field) REQUIRE (f.project_name, f.repo_id, f.owner_fqn, f.name) IS UNIQUE",
            "CREATE CONSTRAINT import_key IF NOT EXISTS FOR (i:Import) REQUIRE (i.project_name, i.repo_id, i.package_fqn, i.target) IS UNIQUE",
            "CREATE CONSTRAINT diff_marker_key IF NOT EXISTS FOR (d:DiffMarker) REQUIRE (d.supergraph_id, d.kind, d.key) IS UNIQUE",
        ]
        for s in stmts:
            try:
                self.run(s)
            except Exception:
                # some Neo4j editions don't allow composite constraints; ignore if unsupported
                pass

    def delete_repo(self, project_name: str, repo_id: str):
        self.run("MATCH (n {project_name:$p, repo_id:$r}) DETACH DELETE n", {"p": project_name, "r": repo_id})

    def repo_stats(self, project_name: str, repo_id: str) -> Dict[str, Any]:
        q = "MATCH (n {project_name:$p, repo_id:$r}) RETURN labels(n) AS labels, count(*) AS cnt"
        rows = self.run(q, {"p": project_name, "r": repo_id})
        out: Dict[str, Any] = {"project_name": project_name, "repo_id": repo_id}
        for r in rows:
            out[":".join(r["labels"])] = r["cnt"]
        return out
