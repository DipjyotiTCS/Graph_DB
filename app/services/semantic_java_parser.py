import json
import os
import subprocess
from typing import Dict, Any, Optional


class SemanticJavaProjectParser:
    """Semantic Java parser wrapper.

    Delegates to the bundled Java CLI (JavaParser + JavaSymbolSolver) that produces
    a *semantic* graph (types, methods, fields, deps, call-edges) as JSON.

    Requirements on the machine running the app:
      - Java 17+
      - Maven (only to build the jar once) OR a prebuilt jar under semantic-parser/target/
    """

    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = repo_root or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _find_jar(self) -> Optional[str]:
        # Common shade outputs
        candidates = [
            os.path.join(self.repo_root, "semantic-parser", "target", "semantic-parser.jar"),
            os.path.join(self.repo_root, "semantic-parser", "target", "semantic-parser-1.0.0-shaded.jar"),
            os.path.join(self.repo_root, "semantic-parser", "target", "semantic-parser-1.0.0.jar"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p

        # Fallback: any jar containing "semantic-parser" in target/
        target = os.path.join(self.repo_root, "semantic-parser", "target")
        if os.path.isdir(target):
            for fn in os.listdir(target):
                if fn.endswith(".jar") and "semantic-parser" in fn:
                    return os.path.join(target, fn)
        return None

    def parse_project(self, project_path: str, project_name: str, repo_id: str) -> Dict[str, Any]:
        jar = self._find_jar()
        if not jar:
            raise RuntimeError(
                "Semantic parser jar not found. Build it first:\n"
                "  cd semantic-parser && mvn -q -DskipTests package\n"
                "Then re-run the ingestion."
            )

        cmd = [
            "java", "-jar", jar,
            "--root", os.path.abspath(project_path),
            "--projectName", project_name,
            "--repoId", repo_id,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode != 0:
            raise RuntimeError(
                "Semantic parser failed.\n"
                f"Command: {' '.join(cmd)}\n"
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}"
            )

        try:
            data: Dict[str, Any] = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                "Semantic parser did not return valid JSON.\n"
                f"STDOUT (first 2000 chars):\n{proc.stdout[:2000]}\n"
                f"STDERR:\n{proc.stderr}"
            ) from e

        # Adapt to the shapes expected by the existing GraphBuilder.
        # GraphBuilder expects:
        #   - graph['types'] as dict keyed by fqn
        #   - graph['extends'] and graph['implements'] as list[tuple(child_fqn, parent_ref)]
        if isinstance(data.get("types"), list):
            data["types"] = {t.get("fqn"): t for t in data.get("types") if isinstance(t, dict) and t.get("fqn")}

        if isinstance(data.get("extends"), list) and data["extends"] and isinstance(data["extends"][0], dict):
            data["extends"] = [
                (x.get("child_fqn"), x.get("parent_ref"))
                for x in data["extends"]
                if x.get("child_fqn") and x.get("parent_ref")
            ]

        if isinstance(data.get("implements"), list) and data["implements"] and isinstance(data["implements"][0], dict):
            data["implements"] = [
                (x.get("child_fqn"), x.get("iface_ref"))
                for x in data["implements"]
                if x.get("child_fqn") and x.get("iface_ref")
            ]

        return data
