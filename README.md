# Java Repo CodeGraph + Cross-Repo SuperGraph (FastAPI + Neo4j)

This service builds a Neo4j code graph from Java source code and supports **cross-repo superimposed graphs**
to compare two repositories and do impact analysis.

## What you asked for
### API 1 — Ingest local folder (already extracted project)
- Reads Java code from a directory path (NOT a zip)
- Builds a code graph in Neo4j with a `repo_id` tag

`POST /ingest/local`

### API 2 — Ingest two Git repos and create a **superimposed graph**
- Clones two repos
- Builds graphs for both
- Creates alignment edges between same FQNs across repos
- Computes a basic **diff** (added/removed/changed classes) using hashes

`POST /ingest/git/superimpose`

### Future — Story-to-change-impact (scaffold included)
- `POST /analysis/story-impact`
- Takes user story description + acceptance criteria
- Returns candidate classes to change (heuristic ranking) using supergraph signals
  (name/annotation match, controller/service/repo, call proximity if available)

> This is a *starting point*. You can later swap in a stronger LLM/embedding-based
> ranker using the Neo4j graph as grounding.

---

## Run with Docker (recommended)
```bash
docker compose up --build
```

Neo4j:
- http://localhost:7474 (browser)
- bolt://localhost:7687 (bolt)

API:
- http://localhost:8000/docs

---

## API usage examples

### 1) Ingest local extracted project
```bash
curl -X POST "http://localhost:8000/ingest/local" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/absolute/path/to/user-management-module-springboot",
    "repo_id": "sb3",
    "project_name": "user-management",
    "overwrite_repo": true
  }'
```

### 2) Ingest two git repos and superimpose
```bash
curl -X POST "http://localhost:8000/ingest/git/superimpose" \
  -H "Content-Type: application/json" \
  -d '{
    "left":  {"repo_url":"https://github.com/org/repoA.git", "branch":"main", "repo_id":"sb3"},
    "right": {"repo_url":"https://github.com/org/repoB.git", "branch":"main", "repo_id":"sb2"},
    "supergraph_id":"sb2_vs_sb3",
    "project_name":"user-management",
    "overwrite_repos": true,
    "overwrite_supergraph": true
  }'
```

### 3) Get a diff summary from Neo4j (after superimpose)
```bash
curl "http://localhost:8000/analysis/diff/sb2_vs_sb3"
```

### 4) Story impact (scaffold)
```bash
curl -X POST "http://localhost:8000/analysis/story-impact" \
  -H "Content-Type: application/json" \
  -d '{
    "supergraph_id":"sb2_vs_sb3",
    "story_title":"Add user lockout after 5 failed logins",
    "description":"As an admin I want lockout to prevent brute force",
    "acceptance_criteria":[
      "After 5 failed attempts, user is locked for 15 minutes",
      "Login endpoint returns 423 Locked when locked"
    ]
  }'
```

---

## Graph Model (Neo4j)

Nodes (per repo):
- `Package {repo_id, project_name, fqn}`
- `Type {repo_id, project_name, fqn, kind, name, annotations, modifiers, file, file_hash}`
- `Method {repo_id, project_name, owner_fqn, signature, name, annotations, modifiers}`
- `Field {repo_id, project_name, owner_fqn, name, type}`
- `Import {repo_id, project_name, package_fqn, target}`

Relationships:
- `(Package)-[:CONTAINS]->(Type)`
- `(Type)-[:DECLARES_METHOD]->(Method)`
- `(Type)-[:DECLARES_FIELD]->(Field)`
- `(Type)-[:IMPORTS]->(Import)`
- `(Type)-[:EXTENDS]->(TypeStub)`
- `(Type)-[:IMPLEMENTS]->(TypeStub)`

Superimposed alignment:
- `(Type {repo_id:left})-[:SAME_FQN {supergraph_id}]->(Type {repo_id:right})`
- Same for `Method` and `Field` when possible.

Diff markers (created during superimpose):
- `(Type)-[:DIFF {supergraph_id, status:'CHANGED'|'UNCHANGED'|'ADDED'|'REMOVED'}]->(:DiffMarker)`
  (Markers are lightweight to query)

---

## Notes
- Java parsing uses `javalang` (AST parse without compilation).
- CALL graph is heuristic (optional). This project focuses on structural diff and alignment first.


### API 2b — Ingest two *local* folders and create a superimposed graph
`POST /ingest/local-superimpose`

This compares two local Java Spring Boot projects (two folders) and writes a superimposed graph + diff markers to Neo4j.

**Parsing**: prefers `java-ast` (`jast`) with fallback to `javalang`.


### Notes
- Imports are resolved to internal classes only (classes present inside the provided project roots). External imports are ignored.
- No Spring-specific labeling is applied; the graph is framework-agnostic.
- Hierarchy is preserved: Project → Class(Type) → Method.




## Semantic parsing (recommended)

This project now **prefers semantic parsing** using **JavaParser + JavaSymbolSolver** to build a *semantic* Neo4j graph.

### What you get with semantic graphs
- Resolved internal types (no import-string guessing)
- Accurate `DEPENDS_ON`, `EXTENDS`, `IMPLEMENTS`
- Real method call edges: `(:Method)-[:CALLS]->(:Method)` (best-effort; only when resolvable)

### Prerequisites (local run)
- **Java 17+** installed (`java -version`)
- **Maven** installed (`mvn -version`)

### Build the semantic parser jar (one-time)
From the repo root:

```bash
cd semantic-parser
mvn -DskipTests package
```

This produces a runnable jar under `semantic-parser/target/` (typically `semantic-parser.jar`).

### How the Python service uses it
During ingestion, `JavaProjectParser.parse_directory(...)` will:

1. Try to run the semantic parser:
   `java -jar semantic-parser/target/semantic-parser.jar --root <project> --projectName <name> --repoId <id>`
2. If Java/Maven/jar are not available, it falls back to the older syntactic parser (javalang/regex).

### Docker
The Dockerfile installs Java 17 + Maven and **builds the semantic parser jar inside the image**, so semantic parsing works out-of-the-box when you run via Docker.

