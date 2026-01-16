from typing import Dict, Any, List, Tuple
from app.services.neo4j_service import Neo4jService

class GraphBuilder:
    """
    Writes a generic Java code graph with hierarchy:

        (Project)-[:HAS_CLASS]->(Type)-[:HAS_METHOD]->(Method)

    Additional relations:
        (Type)-[:DEPENDS_ON]->(Type)
        (Type)-[:EXTENDS]->(Type)   (best-effort by name/fqn resolution)
        (Type)-[:IMPLEMENTS]->(Type)
    """

    def __init__(self, neo: Neo4jService):
        self.neo = neo
        self.neo.ensure_constraints()

    def upsert_repo_graph(self, graph: Dict[str, Any]) -> Dict[str, Any]:
        p = graph["project_name"]
        r = graph["repo_id"]

        # Ensure Project root node
        self.neo.run(
            "MERGE (pr:Project {project_name:$p, repo_id:$r}) SET pr.name=$p",
            {"p": p, "r": r},
        )

        types = list((graph.get("types") or {}).values())
        methods = graph.get("methods", []) or []
        fields = graph.get("fields", []) or []
        deps = graph.get("dependencies", []) or []
        extends = graph.get("extends", []) or []
        implements = graph.get("implements", []) or []

        self._upsert_types(types, p, r)
        self._upsert_methods(methods, p, r)
        self._upsert_fields(fields, p, r)

        # Hierarchy edges
        self._rel_project_has_class(p, r, types)
        self._rel_type_has_method(p, r, methods)

        # Relations
        self._rel_depends_on(deps)
        self._rel_extends(extends, p, r)
        self._rel_implements(implements, p, r)

        calls = graph.get("calls") or []
        self._rel_calls(calls, p, r)

        return {"project_name": p, "repo_id": r}

    def _upsert_types(self, rows: List[Dict[str, Any]], p: str, r: str):
        if not rows:
            return
        q = """UNWIND $rows AS x
        MERGE (t:Type {project_name:x.project_name, repo_id:x.repo_id, fqn:x.fqn})
        SET t.name = x.name,
            t.file = x.file,
            t.file_hash = x.file_hash"""
        self.neo.run(q, {"rows": rows})

    def _upsert_methods(self, rows: List[Dict[str, Any]], p: str, r: str):
        if not rows:
            return
        q = """UNWIND $rows AS x
        MERGE (m:Method {project_name:x.project_name, repo_id:x.repo_id, owner_fqn:x.owner_fqn, signature:x.signature})
        SET m.name = x.name,
            m.file = coalesce(x.file, m.file),
            m.returnType = x.returnType,
            m.param_types = [p IN coalesce(x.params, []) | coalesce(p.type,'')],
            m.param_names = [p IN coalesce(x.params, []) | coalesce(p.name,'')],
            m.beginLine = coalesce(x.beginLine, m.beginLine),
            m.endLine = coalesce(x.endLine, m.endLine),
            m.body_hash = coalesce(x.body_hash, m.body_hash)"""
        self.neo.run(q, {"rows": rows})

    def _upsert_fields(self, rows: List[Dict[str, Any]], p: str, r: str):
        if not rows:
            return
        q = """UNWIND $rows AS x
        MERGE (f:Field {project_name:x.project_name, repo_id:x.repo_id, owner_fqn:x.owner_fqn, name:x.name})
        SET f.type = x.type"""
        self.neo.run(q, {"rows": rows})

    def _rel_project_has_class(self, p: str, r: str, types: List[Dict[str, Any]]):
        if not types:
            return
        q = """UNWIND $rows AS x
        MATCH (pr:Project {project_name:$p, repo_id:$r})
        MATCH (t:Type {project_name:x.project_name, repo_id:x.repo_id, fqn:x.fqn})
        MERGE (pr)-[:HAS_CLASS]->(t)"""
        self.neo.run(q, {"rows": types, "p": p, "r": r})

    def _rel_type_has_method(self, p: str, r: str, methods: List[Dict[str, Any]]):
        if not methods:
            return
        q = """UNWIND $rows AS x
        MATCH (t:Type {project_name:x.project_name, repo_id:x.repo_id, fqn:x.owner_fqn})
        MATCH (m:Method {project_name:x.project_name, repo_id:x.repo_id, owner_fqn:x.owner_fqn, signature:x.signature})
        MERGE (t)-[:HAS_METHOD]->(m)"""
        self.neo.run(q, {"rows": methods})

    def _rel_depends_on(self, deps: List[Dict[str, Any]]):
        if not deps:
            return
        q = """UNWIND $rows AS d
        MATCH (src:Type {project_name:d.project_name, repo_id:d.repo_id, fqn:d.from_fqn})
        MATCH (dst:Type {project_name:d.project_name, repo_id:d.repo_id, fqn:d.to_fqn})
        MERGE (src)-[rel:DEPENDS_ON]->(dst)
        SET rel.via = d.via, rel.file = d.file"""
        self.neo.run(q, {"rows": deps})

    def _rel_extends(self, pairs: List[Tuple[str, str]], p: str, r: str):
        if not pairs:
            return
        q = """UNWIND $pairs AS x
        WITH x[0] AS child_fqn, x[1] AS parent_ref
        MATCH (c:Type {project_name:$p, repo_id:$r, fqn:child_fqn})
        MATCH (p2:Type {project_name:$p, repo_id:$r})
        WHERE p2.fqn = parent_ref OR p2.name = parent_ref OR p2.fqn ENDS WITH ('.' + parent_ref) OR p2.fqn ENDS WITH ('$' + parent_ref)
        MERGE (c)-[:EXTENDS]->(p2)"""
        self.neo.run(q, {"pairs": pairs, "p": p, "r": r})

    def _rel_implements(self, pairs: List[Tuple[str, str]], p: str, r: str):
        if not pairs:
            return
        q = """UNWIND $pairs AS x
        WITH x[0] AS child_fqn, x[1] AS iface_ref
        MATCH (c:Type {project_name:$p, repo_id:$r, fqn:child_fqn})
        MATCH (i:Type {project_name:$p, repo_id:$r})
        WHERE i.fqn = iface_ref OR i.name = iface_ref OR i.fqn ENDS WITH ('.' + iface_ref) OR i.fqn ENDS WITH ('$' + iface_ref)
        MERGE (c)-[:IMPLEMENTS]->(i)"""
        self.neo.run(q, {"pairs": pairs, "p": p, "r": r})


    def _rel_calls(self, calls: List[Dict[str, Any]], p: str, r: str):
        """Create (Method)-[:CALLS]->(Method) edges.

        Expected call row keys (as produced by the bundled semantic parser):
          - project_name, repo_id
          - from_owner_fqn, from_signature
          - to_owner_fqn, to_signature
          - file (optional)
        """
        if not calls:
            return

        q = """UNWIND $rows AS c
        MATCH (src:Method {project_name:c.project_name, repo_id:c.repo_id, owner_fqn:c.from_owner_fqn, signature:c.from_signature})
        MATCH (dst:Method {project_name:c.project_name, repo_id:c.repo_id, owner_fqn:c.to_owner_fqn, signature:c.to_signature})
        MERGE (src)-[rel:CALLS]->(dst)
        SET rel.file = coalesce(c.file, rel.file),
            rel.arg_exprs = coalesce(c.arg_exprs, rel.arg_exprs),
            rel.arg_types = coalesce(c.arg_types, rel.arg_types)"""
        self.neo.run(q, {"rows": calls})

