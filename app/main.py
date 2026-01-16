from fastapi import FastAPI
from app.routers.ingest import router as ingest_router
from app.routers.analysis import router as analysis_router

app = FastAPI(
    title="Java SuperGraph Builder",
    version="1.0.0",
    description="Build Neo4j code graphs from Java repos and superimpose two repos for cross-repo analysis."
)

app.include_router(ingest_router)
app.include_router(analysis_router)

@app.get("/health")
def health():
    return {"status": "ok"}
