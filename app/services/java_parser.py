import os
import re
import hashlib
from typing import Dict, Any, List, Optional, Set, Tuple, DefaultDict
from collections import defaultdict
from app.services.semantic_java_parser import SemanticJavaProjectParser

JAVA_FILE_RE = re.compile(r".+\.java$", re.IGNORECASE)

def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def _file_hash(path: str) -> str:
    return hashlib.sha1(_read_bytes(path)).hexdigest()

def _relpath(root: str, path: str) -> str:
    try:
        return os.path.relpath(path, root).replace("\\", "/")
    except Exception:
        return path

def _package_from_regex(text: str) -> str:
    m = re.search(r"^\s*package\s+([a-zA-Z0-9_.]+)\s*;", text, flags=re.MULTILINE)
    return m.group(1) if m else ""

def _imports_from_regex(text: str) -> List[str]:
    out: List[str] = []
    for m in re.finditer(r"^\s*import\s+(static\s+)?([a-zA-Z0-9_.*]+)\s*;", text, flags=re.MULTILINE):
        target = m.group(2).strip()
        out.append(target)
    return out

def _safe_list(x):
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        return list(x)
    return [x]

def _node_name(node) -> Optional[str]:
    for k in ("name", "id", "identifier"):
        if hasattr(node, k):
            v = getattr(node, k)
            if isinstance(v, str):
                return v
    return None

def _type_ref_to_str(t) -> Optional[str]:
    if t is None:
        return None
    if isinstance(t, str):
        return t
    nm = _node_name(t)
    if nm:
        return nm
    if hasattr(t, "qname"):
        q = getattr(t, "qname")
        if isinstance(q, str):
            return q
        if hasattr(q, "name"):
            return str(getattr(q, "name"))
    return None

def _iter_children(node):
    if node is None:
        return
    if isinstance(node, (str, int, float, bool, bytes)):
        return
    if isinstance(node, dict):
        for v in node.values():
            yield v
        return
    if isinstance(node, (list, tuple, set)):
        for v in node:
            yield v
        return
    d = getattr(node, "__dict__", None)
    if isinstance(d, dict):
        for v in d.values():
            yield v

def _is_type_decl(node) -> bool:
    n = node.__class__.__name__
    return (
        n in ("Class", "ClassDecl", "ClassDeclaration", "Interface", "InterfaceDecl", "InterfaceDeclaration",
              "Enum", "EnumDecl", "EnumDeclaration", "AnnotationDeclaration")
        or ("Class" in n and "Declaration" in n)
        or ("Interface" in n and "Declaration" in n)
        or ("Enum" in n and "Declaration" in n)
    )

def _is_method_decl(node) -> bool:
    n = node.__class__.__name__
    return ("Method" in n and "Invocation" not in n) or n in ("Constructor", "ConstructorDeclaration")

def _is_field_decl(node) -> bool:
    n = node.__class__.__name__
    return ("Field" in n and "Declaration" in n) or n in ("Field", "FieldDecl")

def _extract_methods(node, owner_fqn: str, project_name: str, repo_id: str, out: List[Dict[str, Any]]):
    name = _node_name(node) or "anonymous"
    params = []
    if hasattr(node, "parameters"):
        for p in _safe_list(getattr(node, "parameters")):
            ptype = _type_ref_to_str(getattr(p, "type", None)) or _type_ref_to_str(getattr(p, "jtype", None)) or "?"
            pname = _node_name(p) or ""
            params.append({"name": pname, "type": ptype})
    signature = f"{name}(" + ",".join([p["type"] for p in params]) + ")"
    rtype = _type_ref_to_str(getattr(node, "return_type", None)) or _type_ref_to_str(getattr(node, "returnType", None)) or _type_ref_to_str(getattr(node, "type", None)) or "void"
    out.append({
        "project_name": project_name,
        "repo_id": repo_id,
        "owner_fqn": owner_fqn,
        "name": name,
        "signature": signature,
        "returnType": rtype,
        "params": params,
    })

def _extract_fields(node, owner_fqn: str, project_name: str, repo_id: str, out: List[Dict[str, Any]], out_field_types: List[str]):
    ftype = _type_ref_to_str(getattr(node, "type", None)) or _type_ref_to_str(getattr(node, "jtype", None)) or "?"
    declarators = []
    for k in ("declarators", "vars", "variables", "declarations"):
        if hasattr(node, k):
            declarators = _safe_list(getattr(node, k))
            break
    if not declarators and hasattr(node, "name"):
        declarators = [node]
    for decl in declarators:
        nm = _node_name(decl)
        if not nm:
            continue
        out.append({
            "project_name": project_name,
            "repo_id": repo_id,
            "owner_fqn": owner_fqn,
            "name": nm,
            "type": ftype,
        })
        out_field_types.append(ftype)

class JavaProjectParser:
    """
    Generic Java parser producing a project->class->method hierarchy and internal-only dependencies.

    Key behavioral rules (as requested):
      1) Imports are used ONLY to link to classes that exist inside the provided project roots.
         External imports (e.g., org.springframework..., java.util.List) are ignored.
         Also, we do not store/import package names; we only resolve to internal classes.
      2) No Spring-specific processing: annotations are not interpreted or used for labeling.
      3) Hierarchy is preserved via graph builder: Project -> Type -> Method
    """

    def __init__(self):
        self._semantic = SemanticJavaProjectParser()
        try:
            import jast  # type: ignore
            self._jast = jast
        except Exception:
            self._jast = None
        try:
            import javalang  # type: ignore
            self._javalang = javalang
        except Exception:
            self._javalang = None

    def _parse_unit(self, text: str):
        if self._jast is not None:
            try:
                return self._jast.parse(text), "jast"
            except Exception:
                pass
        if self._javalang is not None:
            try:
                return self._javalang.parse.parse(text), "javalang"
            except Exception:
                pass
        return None, "regex"

    def _extract_package_and_imports(self, unit, text: str) -> Tuple[str, List[str]]:
        pkg = ""
        imports: List[str] = []
        if unit is not None:
            # package
            if hasattr(unit, "package") and unit.package:
                pkg = _type_ref_to_str(getattr(unit.package, "name", None)) or _type_ref_to_str(getattr(unit.package, "qname", None)) or _node_name(unit.package) or ""
            elif hasattr(unit, "package") and isinstance(unit.package, str):
                pkg = unit.package
            # imports
            if hasattr(unit, "imports") and unit.imports:
                for im in _safe_list(unit.imports):
                    # javalang: im.path
                    if hasattr(im, "path"):
                        imports.append(str(getattr(im, "path")))
                        continue
                    target = _type_ref_to_str(getattr(im, "name", None)) or _type_ref_to_str(getattr(im, "qname", None)) or _node_name(im)
                    if target:
                        imports.append(str(target))
        if not pkg:
            pkg = _package_from_regex(text)
        if not imports:
            imports = _imports_from_regex(text)
        return pkg, imports

    def _discover_types_in_unit(self, unit, pkg: str) -> List[Tuple[str, str]]:
        """Return list of (simple_name, fqn) for all types in unit."""
        discovered: List[Tuple[str, str]] = []

        if unit is None:
            return discovered

        def walk(node, outer_fqn: Optional[str] = None):
            if _is_type_decl(node):
                name = _node_name(node) or "Anonymous"
                fqn = f"{pkg}.{name}" if pkg else name
                if outer_fqn:
                    fqn = f"{outer_fqn}${name}"
                discovered.append((name, fqn))

                # dive into body for nested types
                body = None
                for k in ("body", "members", "declarations"):
                    if hasattr(node, k):
                        body = getattr(node, k)
                        break
                for child in _safe_list(body):
                    walk(child, outer_fqn=fqn)
                return

            for c in _iter_children(node):
                if isinstance(c, (list, tuple, set)):
                    for cc in c:
                        walk(cc, outer_fqn=outer_fqn)
                else:
                    walk(c, outer_fqn=outer_fqn)

        walk(unit)
        return discovered

    def parse_directory(self, root_dir: str, project_name: str, repo_id: str) -> Dict[str, Any]:
        # Prefer semantic parsing (JavaParser + SymbolSolver) when available.
        # This produces resolved types and call-edges, enabling a true semantic graph.
        try:
            return self._semantic.parse_project(root_dir, project_name, repo_id)
        except Exception:
            # Fallback to syntactic parsing (javalang/regex) for environments without Java/Maven.
            pass

        graph: Dict[str, Any] = {
            "project_name": project_name,
            "repo_id": repo_id,
            "types": {},        # fqn -> {project_name, repo_id, name, fqn, file, file_hash}
            "methods": [],      # {project_name, repo_id, owner_fqn, signature, ...}
            "fields": [],       # {project_name, repo_id, owner_fqn, name, type}
            "dependencies": [], # {project_name, repo_id, from_fqn, to_fqn, to_simple, via, file}
            "extends": [],      # (child_fqn, parent_simple_or_fqn)
            "implements": [],   # (child_fqn, iface_simple_or_fqn)
            "stats": {"java_files": 0, "parse_errors": 0, "parser_pref": "jast" if self._jast else ("javalang" if self._javalang else "regex")},
        }

        # collect java files
        java_files: List[str] = []
        for dp, _, files in os.walk(root_dir):
            for fn in files:
                if JAVA_FILE_RE.match(fn):
                    java_files.append(os.path.join(dp, fn))
        graph["stats"]["java_files"] = len(java_files)

        # pass 1: discover ALL internal types to resolve imports to internal only
        internal_fqn_set: Set[str] = set()
        internal_simple_to_fqns: DefaultDict[str, List[str]] = defaultdict(list)

        units_cache: Dict[str, Any] = {}
        pkg_cache: Dict[str, str] = {}
        imports_cache: Dict[str, List[str]] = {}

        for jf in java_files:
            text = _read_text(jf)
            unit, _ = self._parse_unit(text)
            pkg, imports = self._extract_package_and_imports(unit, text)
            units_cache[jf] = unit
            pkg_cache[jf] = pkg
            imports_cache[jf] = imports

            for simple, fqn in self._discover_types_in_unit(unit, pkg):
                internal_fqn_set.add(fqn)
                internal_simple_to_fqns[simple].append(fqn)

        # helper to resolve import target to an internal fqn (if any)
        def resolve_import_to_internal_fqn(import_path: str) -> Optional[str]:
            if import_path.endswith(".*"):
                # wildcard imports: do not create dependencies without package parsing (requested).
                return None
            # exact match by internal fqn (best)
            if import_path in internal_fqn_set:
                return import_path
            simple = import_path.split(".")[-1]
            if simple in internal_simple_to_fqns:
                # if multiple matches, pick the first (projects should avoid duplicates)
                return internal_simple_to_fqns[simple][0]
            return None

        # pass 2: build types/methods/fields and internal-only dependencies
        for jf in java_files:
            file_rel = _relpath(root_dir, jf)
            fh = _file_hash(jf)
            text = _read_text(jf)

            unit = units_cache.get(jf)
            pkg = pkg_cache.get(jf) or _package_from_regex(text)
            imports = imports_cache.get(jf) or _imports_from_regex(text)

            if unit is None:
                graph["stats"]["parse_errors"] += 1
                continue

            # record file-level resolved import deps; applied to each top-level type declared in this file
            resolved_import_fqns: List[str] = []
            for imp in imports:
                fqn = resolve_import_to_internal_fqn(imp.strip())
                if fqn:
                    resolved_import_fqns.append(fqn)

            def walk(node, outer_fqn: Optional[str] = None, file_top_level_types: Optional[List[str]] = None):
                if file_top_level_types is None:
                    file_top_level_types = []

                if _is_type_decl(node):
                    name = _node_name(node) or "Anonymous"
                    fqn = f"{pkg}.{name}" if pkg else name
                    if outer_fqn:
                        fqn = f"{outer_fqn}${name}"

                    graph["types"][fqn] = {
                        "project_name": project_name,
                        "repo_id": repo_id,
                        "name": name,
                        "fqn": fqn,
                        "file": file_rel,
                        "file_hash": fh,
                    }

                    # extends/implements
                    ext = None
                    for k in ("extends", "extend", "superclass"):
                        if hasattr(node, k):
                            ext = getattr(node, k)
                            break
                    ext_name = _type_ref_to_str(ext)
                    if ext_name:
                        graph["extends"].append((fqn, ext_name))

                    impls = None
                    for k in ("implements", "interfaces"):
                        if hasattr(node, k):
                            impls = getattr(node, k)
                            break
                    for it in _safe_list(impls):
                        itn = _type_ref_to_str(it)
                        if itn:
                            graph["implements"].append((fqn, itn))

                    # members
                    body = None
                    for k in ("body", "members", "declarations"):
                        if hasattr(node, k):
                            body = getattr(node, k)
                            break

                    # field types collected to form deps
                    field_types: List[str] = []

                    for child in _safe_list(body):
                        if child is None:
                            continue
                        if _is_type_decl(child):
                            walk(child, outer_fqn=fqn, file_top_level_types=file_top_level_types)
                        elif _is_method_decl(child):
                            _extract_methods(child, fqn, project_name, repo_id, graph["methods"])
                        elif _is_field_decl(child):
                            _extract_fields(child, fqn, project_name, repo_id, graph["fields"], field_types)
                        else:
                            walk(child, outer_fqn=fqn, file_top_level_types=file_top_level_types)

                    # Dependencies:
                    # (a) resolved imports from the file (internal only)
                    for to_fqn in resolved_import_fqns:
                        graph["dependencies"].append({
                            "project_name": project_name,
                            "repo_id": repo_id,
                            "from_fqn": fqn,
                            "to_fqn": to_fqn,
                            "to_simple": to_fqn.split(".")[-1],
                            "via": "import",
                            "file": file_rel,
                        })

                    # (b) field-type deps, but only if internal
                    for tname in field_types:
                        # strip generics: List<User> -> User
                        base = re.sub(r"<.*?>", "", tname).strip()
                        base = base.split(".")[-1]
                        if base in internal_simple_to_fqns:
                            to_fqn = internal_simple_to_fqns[base][0]
                            graph["dependencies"].append({
                                "project_name": project_name,
                                "repo_id": repo_id,
                                "from_fqn": fqn,
                                "to_fqn": to_fqn,
                                "to_simple": base,
                                "via": "field",
                                "file": file_rel,
                            })

                    return

                for c in _iter_children(node):
                    if isinstance(c, (list, tuple, set)):
                        for cc in c:
                            walk(cc, outer_fqn=outer_fqn, file_top_level_types=file_top_level_types)
                    else:
                        walk(c, outer_fqn=outer_fqn, file_top_level_types=file_top_level_types)

            walk(unit)

        return graph
