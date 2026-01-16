from typing import Dict, Any, List, Tuple, Optional

import difflib
import os
from app.services.neo4j_service import Neo4jService

class SuperimposeService:
    """
    Creates a "supergraph" overlay between two repos inside the same Neo4j DB.

    Alignment edges:
      - SAME_FQN between Type nodes with equal fqn across repos
      - SAME_SIGNATURE between Method nodes with equal (owner_fqn, signature) across repos
      - SAME_FIELD between Field nodes with equal (owner_fqn, name) across repos

    Diff markers:
      - DiffMarker {supergraph_id, kind, key, status}
      - A node has a DIFF relationship to its marker.
    """

    def __init__(self, neo: Neo4jService):
        self.neo = neo
        self.neo.ensure_constraints()

    def delete_supergraph(self, supergraph_id: str):
        self.neo.run("MATCH ()-[r:SAME_FQN {supergraph_id:$sid}]-() DELETE r", {"sid": supergraph_id})
        self.neo.run("MATCH ()-[r:SAME_SIGNATURE {supergraph_id:$sid}]-() DELETE r", {"sid": supergraph_id})
        self.neo.run("MATCH ()-[r:SAME_FIELD {supergraph_id:$sid}]-() DELETE r", {"sid": supergraph_id})
        self.neo.run("MATCH ()-[r:DIFF {supergraph_id:$sid}]->() DELETE r", {"sid": supergraph_id})
        self.neo.run("MATCH (d:DiffMarker {supergraph_id:$sid}) DETACH DELETE d", {"sid": supergraph_id})

    def superimpose_and_diff(
        self,
        project_name: str,
        left_repo_id: str,
        right_repo_id: str,
        supergraph_id: str,
        left_root: Optional[str] = None,
        right_root: Optional[str] = None,
        max_diff_chars: int = 50000,
    ) -> Dict[str, Any]:
        # 1) align Types by exact FQN
        self.neo.run(
            """MATCH (l:Type {project_name:$p, repo_id:$l})
               MATCH (r:Type {project_name:$p, repo_id:$r, fqn:l.fqn})
               MERGE (l)-[:SAME_FQN {supergraph_id:$sid}]->(r)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )

        # 2) align Methods by (owner_fqn, signature)
        self.neo.run(
            """MATCH (l:Method {project_name:$p, repo_id:$l})
               MATCH (r:Method {project_name:$p, repo_id:$r, owner_fqn:l.owner_fqn, signature:l.signature})
               MERGE (l)-[:SAME_SIGNATURE {supergraph_id:$sid}]->(r)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )

        # 3) align Fields by (owner_fqn, name)
        self.neo.run(
            """MATCH (l:Field {project_name:$p, repo_id:$l})
               MATCH (r:Field {project_name:$p, repo_id:$r, owner_fqn:l.owner_fqn, name:l.name})
               MERGE (l)-[:SAME_FIELD {supergraph_id:$sid}]->(r)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )

        # 4) Diff markers for Types
        # unchanged/changed for matched
        self.neo.run(
            """MATCH (l:Type {project_name:$p, repo_id:$l})-[:SAME_FQN {supergraph_id:$sid}]->(r:Type {project_name:$p, repo_id:$r})
               WITH l,r,
                    CASE WHEN coalesce(l.file_hash,'') = coalesce(r.file_hash,'') THEN 'UNCHANGED' ELSE 'CHANGED' END AS status
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Type', key:l.fqn})
               SET d.status=status, d.fqn=l.fqn
               MERGE (l)-[:DIFF {supergraph_id:$sid}]->(d)
               MERGE (r)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )
        # removed (only in left)
        self.neo.run(
            """MATCH (l:Type {project_name:$p, repo_id:$l})
               WHERE NOT EXISTS { MATCH (l)-[:SAME_FQN {supergraph_id:$sid}]->(:Type {project_name:$p, repo_id:$r}) }
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Type', key:l.fqn})
               SET d.status='REMOVED', d.fqn=l.fqn
               MERGE (l)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )
        # added (only in right)
        self.neo.run(
            """MATCH (r:Type {project_name:$p, repo_id:$r})
               WHERE NOT EXISTS { MATCH (:Type {project_name:$p, repo_id:$l, fqn:r.fqn})-[:SAME_FQN {supergraph_id:$sid}]->(r) }
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Type', key:r.fqn})
               SET d.status='ADDED', d.fqn=r.fqn
               MERGE (r)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )

        # 5) Diff markers for Methods
        self.neo.run(
            """MATCH (l:Method {project_name:$p, repo_id:$l})-[:SAME_SIGNATURE {supergraph_id:$sid}]->(r:Method {project_name:$p, repo_id:$r})
               WITH l,r,
                    CASE WHEN coalesce(l.returnType,'') = coalesce(r.returnType,'')
                          AND coalesce(toString(l.params),'') = coalesce(toString(r.params),'')
                          AND coalesce(toString(l.modifiers),'') = coalesce(toString(r.modifiers),'')
                          AND coalesce(l.body_hash,'') = coalesce(r.body_hash,'')
                          THEN 'UNCHANGED' ELSE 'CHANGED' END AS status
               WITH l,r,status, l.owner_fqn + '::' + l.signature AS k
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Method', key:k})
               SET d.status=status, d.fqn=k
               MERGE (l)-[:DIFF {supergraph_id:$sid}]->(d)
               MERGE (r)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )
        self.neo.run(
            """MATCH (l:Method {project_name:$p, repo_id:$l})
               WHERE NOT EXISTS { MATCH (l)-[:SAME_SIGNATURE {supergraph_id:$sid}]->(:Method {project_name:$p, repo_id:$r}) }
               WITH l, l.owner_fqn + '::' + l.signature AS k
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Method', key:k})
               SET d.status='REMOVED', d.fqn=k
               MERGE (l)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )
        self.neo.run(
            """MATCH (r:Method {project_name:$p, repo_id:$r})
               WHERE NOT EXISTS { MATCH (:Method {project_name:$p, repo_id:$l, owner_fqn:r.owner_fqn, signature:r.signature})-[:SAME_SIGNATURE {supergraph_id:$sid}]->(r) }
               WITH r, r.owner_fqn + '::' + r.signature AS k
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Method', key:k})
               SET d.status='ADDED', d.fqn=k
               MERGE (r)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )

        # 6) Diff markers for Fields
        self.neo.run(
            """MATCH (l:Field {project_name:$p, repo_id:$l})-[:SAME_FIELD {supergraph_id:$sid}]->(r:Field {project_name:$p, repo_id:$r})
               WITH l,r,
                    CASE WHEN coalesce(l.type,'') = coalesce(r.type,'')
                          AND coalesce(toString(l.modifiers),'') = coalesce(toString(r.modifiers),'')
                          THEN 'UNCHANGED' ELSE 'CHANGED' END AS status
               WITH l,r,status, l.owner_fqn + '::' + l.name AS k
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Field', key:k})
               SET d.status=status, d.fqn=k
               MERGE (l)-[:DIFF {supergraph_id:$sid}]->(d)
               MERGE (r)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )
        self.neo.run(
            """MATCH (l:Field {project_name:$p, repo_id:$l})
               WHERE NOT EXISTS { MATCH (l)-[:SAME_FIELD {supergraph_id:$sid}]->(:Field {project_name:$p, repo_id:$r}) }
               WITH l, l.owner_fqn + '::' + l.name AS k
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Field', key:k})
               SET d.status='REMOVED', d.fqn=k
               MERGE (l)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )
        self.neo.run(
            """MATCH (r:Field {project_name:$p, repo_id:$r})
               WHERE NOT EXISTS { MATCH (:Field {project_name:$p, repo_id:$l, owner_fqn:r.owner_fqn, name:r.name})-[:SAME_FIELD {supergraph_id:$sid}]->(r) }
               WITH r, r.owner_fqn + '::' + r.name AS k
               MERGE (d:DiffMarker {supergraph_id:$sid, kind:'Field', key:k})
               SET d.status='ADDED', d.fqn=k
               MERGE (r)-[:DIFF {supergraph_id:$sid}]->(d)""",
            {"p": project_name, "l": left_repo_id, "r": right_repo_id, "sid": supergraph_id},
        )

        # Optionally attach actual file diffs to CHANGED markers (helps UI/debugging).
        if left_root and right_root:
            self._attach_file_diffs(
                supergraph_id=supergraph_id,
                project_name=project_name,
                left_repo_id=left_repo_id,
                right_repo_id=right_repo_id,
                left_root=left_root,
                right_root=right_root,
                max_chars=max_diff_chars,
            )

        return self.diff_summary(supergraph_id)

    def _attach_file_diffs(
        self,
        supergraph_id: str,
        project_name: str,
        left_repo_id: str,
        right_repo_id: str,
        left_root: str,
        right_root: str,
        max_chars: int = 50000,
    ):
        """Store actual unified diffs on DiffMarker nodes.

        We compute diffs at the *file* level for markers that are CHANGED.
        This is resilient and cheap, and still gives you the exact patch you'd see from git.
        """

        left_root = os.path.abspath(left_root)
        right_root = os.path.abspath(right_root)

        # ---- Type diffs (use Type.file)
        type_rows = self.neo.run(
            """MATCH (d:DiffMarker {supergraph_id:$sid, kind:'Type', status:'CHANGED'})
               OPTIONAL MATCH (l:Type {project_name:$p, repo_id:$lrepo})-[:DIFF {supergraph_id:$sid}]->(d)
               OPTIONAL MATCH (r:Type {project_name:$p, repo_id:$rrepo})-[:DIFF {supergraph_id:$sid}]->(d)
               RETURN d.key AS key, coalesce(l.file,'') AS left_file, coalesce(r.file,'') AS right_file""",
            {"sid": supergraph_id, "p": project_name, "lrepo": left_repo_id, "rrepo": right_repo_id},
        )
        for row in type_rows:
            key = row.get("key")
            lf = row.get("left_file") or ""
            rf = row.get("right_file") or ""
            patch = self._unified_diff_for_files(left_root, right_root, lf, rf, max_chars=max_chars)
            if patch:
                self.neo.run(
                    """MATCH (d:DiffMarker {supergraph_id:$sid, kind:'Type', key:$key})
                       SET d.diff = $diff, d.left_file = $lf, d.right_file = $rf""",
                    {"sid": supergraph_id, "key": key, "diff": patch, "lf": lf, "rf": rf},
                )

        # ---- Method diffs (method-only diff using stored begin/end line ranges)
        method_rows = self.neo.run(
            """MATCH (d:DiffMarker {supergraph_id:$sid, kind:'Method', status:'CHANGED'})
               OPTIONAL MATCH (l:Method {project_name:$p, repo_id:$lrepo})-[:DIFF {supergraph_id:$sid}]->(d)
               OPTIONAL MATCH (r:Method {project_name:$p, repo_id:$rrepo})-[:DIFF {supergraph_id:$sid}]->(d)
               OPTIONAL MATCH (lt:Type {project_name:$p, repo_id:$lrepo, fqn:l.owner_fqn})
               OPTIONAL MATCH (rt:Type {project_name:$p, repo_id:$rrepo, fqn:r.owner_fqn})
               RETURN d.key AS key,
                      coalesce(l.file, lt.file, '') AS left_file,
                      coalesce(r.file, rt.file, '') AS right_file,
                      coalesce(l.beginLine, 0) AS left_begin,
                      coalesce(l.endLine, 0) AS left_end,
                      coalesce(r.beginLine, 0) AS right_begin,
                      coalesce(r.endLine, 0) AS right_end""",
            {"sid": supergraph_id, "p": project_name, "lrepo": left_repo_id, "rrepo": right_repo_id},
        )
        for row in method_rows:
            key = row.get("key")
            lf = row.get("left_file") or ""
            rf = row.get("right_file") or ""
            lb = int(row.get("left_begin") or 0)
            le = int(row.get("left_end") or 0)
            rb = int(row.get("right_begin") or 0)
            re_ = int(row.get("right_end") or 0)

            # If we don't have ranges, fall back to file diff (still better than nothing)
            if lb > 0 and le >= lb and rb > 0 and re_ >= rb:
                patch = self._unified_diff_for_file_ranges(
                    left_root, right_root, lf, rf, lb, le, rb, re_, max_chars=max_chars
                )
            else:
                patch = self._unified_diff_for_files(left_root, right_root, lf, rf, max_chars=max_chars)

            if patch:
                self.neo.run(
                    """MATCH (d:DiffMarker {supergraph_id:$sid, kind:'Method', key:$key})
                       SET d.diff = $diff,
                           d.left_file = $lf, d.right_file = $rf,
                           d.left_begin = $lb, d.left_end = $le,
                           d.right_begin = $rb, d.right_end = $re""",
                    {"sid": supergraph_id, "key": key, "diff": patch, "lf": lf, "rf": rf,
                     "lb": lb, "le": le, "rb": rb, "re": re_},
                )
# ---- Field diffs (use owner Type.file; Field doesn't always have file)
        field_rows = self.neo.run(
            """MATCH (d:DiffMarker {supergraph_id:$sid, kind:'Field', status:'CHANGED'})
               OPTIONAL MATCH (l:Field {project_name:$p, repo_id:$lrepo})-[:DIFF {supergraph_id:$sid}]->(d)
               OPTIONAL MATCH (r:Field {project_name:$p, repo_id:$rrepo})-[:DIFF {supergraph_id:$sid}]->(d)
               OPTIONAL MATCH (lt:Type {project_name:$p, repo_id:$lrepo, fqn:l.owner_fqn})
               OPTIONAL MATCH (rt:Type {project_name:$p, repo_id:$rrepo, fqn:r.owner_fqn})
               RETURN d.key AS key, coalesce(lt.file,'') AS left_file, coalesce(rt.file,'') AS right_file""",
            {"sid": supergraph_id, "p": project_name, "lrepo": left_repo_id, "rrepo": right_repo_id},
        )
        for row in field_rows:
            key = row.get("key")
            lf = row.get("left_file") or ""
            rf = row.get("right_file") or ""
            patch = self._unified_diff_for_files(left_root, right_root, lf, rf, max_chars=max_chars)
            if patch:
                self.neo.run(
                    """MATCH (d:DiffMarker {supergraph_id:$sid, kind:'Field', key:$key})
                       SET d.diff = $diff, d.left_file = $lf, d.right_file = $rf""",
                    {"sid": supergraph_id, "key": key, "diff": patch, "lf": lf, "rf": rf},
                )

    def _unified_diff_for_files(self, left_root: str, right_root: str, left_rel: str, right_rel: str, max_chars: int = 50000) -> str:
        """Compute a git-like unified diff for two files addressed by relative path."""

        # If one side is missing, still produce a /dev/null style diff.
        left_path = os.path.join(left_root, left_rel) if left_rel else None
        right_path = os.path.join(right_root, right_rel) if right_rel else None

        left_lines: List[str] = []
        right_lines: List[str] = []

        if left_path and os.path.exists(left_path) and os.path.isfile(left_path):
            with open(left_path, "r", encoding="utf-8", errors="ignore") as f:
                left_lines = f.read().splitlines(True)

        if right_path and os.path.exists(right_path) and os.path.isfile(right_path):
            with open(right_path, "r", encoding="utf-8", errors="ignore") as f:
                right_lines = f.read().splitlines(True)

        a_name = f"a/{left_rel or 'dev/null'}"
        b_name = f"b/{right_rel or 'dev/null'}"

        diff_iter = difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=a_name,
            tofile=b_name,
            lineterm="",
        )
        patch = "\n".join(diff_iter)
        if not patch.strip():
            return ""
        if len(patch) > max_chars:
            patch = patch[: max_chars] + "\n... (diff truncated)"
        return patch


    def _unified_diff_for_file_ranges(
        self,
        left_root: str,
        right_root: str,
        left_rel: str,
        right_rel: str,
        left_begin: int,
        left_end: int,
        right_begin: int,
        right_end: int,
        max_chars: int = 50000,
        context: int = 3,
    ) -> str:
        """Compute a unified diff for *slices* of two files (1-indexed line ranges).

        Used for method-level diffs so we don't store huge full-file patches on every method marker.
        """
        # Read full files (or empty if missing), then slice.
        left_path = os.path.join(left_root, left_rel) if left_rel else ""
        right_path = os.path.join(right_root, right_rel) if right_rel else ""

        def _read_lines(path: str) -> List[str]:
            if not path or not os.path.exists(path):
                return []
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read().splitlines(True)  # keep \n
            except Exception:
                return []

        l_all = _read_lines(left_path)
        r_all = _read_lines(right_path)

        # Clamp ranges to file size
        lb = max(1, left_begin)
        le = max(lb, left_end)
        rb = max(1, right_begin)
        re_ = max(rb, right_end)

        l_slice = l_all[lb - 1 : le] if l_all else []
        r_slice = r_all[rb - 1 : re_] if r_all else []

        # Provide helpful labels like git does
        l_label = f"a/{left_rel}:{lb}-{le}" if left_rel else "/dev/null"
        r_label = f"b/{right_rel}:{rb}-{re_}" if right_rel else "/dev/null"

        diff_lines = difflib.unified_diff(
            l_slice,
            r_slice,
            fromfile=l_label,
            tofile=r_label,
            n=context,
            lineterm="",
        )
        patch = "\n".join(diff_lines)
        if len(patch) > max_chars:
            patch = patch[:max_chars] + "\n... (diff truncated)"
        return patch

    def diff_summary(self, supergraph_id: str) -> Dict[str, Any]:
        q = """MATCH (d:DiffMarker {supergraph_id:$sid})
               RETURN d.kind as kind, d.status as status, count(*) as cnt"""
        rows = self.neo.run(q, {"sid": supergraph_id})
        out: Dict[str, Any] = {"supergraph_id": supergraph_id, "counts": {}}
        for r in rows:
            out["counts"].setdefault(r["kind"], {})
            out["counts"][r["kind"]][r["status"]] = r["cnt"]

        q2 = """MATCH (d:DiffMarker {supergraph_id:$sid, status:'CHANGED'})
                RETURN d.kind as kind, d.fqn as fqn LIMIT 50"""
        out["sample_changed"] = [{"kind": r["kind"], "key": r["fqn"]} for r in self.neo.run(q2, {"sid": supergraph_id})]
        return out
