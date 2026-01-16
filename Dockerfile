FROM python:3.11-slim

# --- System deps for semantic parsing (JavaParser + SymbolSolver) ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jdk maven git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App code
COPY app /app/app
COPY semantic-parser /app/semantic-parser
COPY README.md /app/README.md
COPY .env.example /app/.env.example

# Build the semantic parser jar inside the image so runtime doesn't need Maven.
RUN cd /app/semantic-parser && mvn -q -DskipTests package

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
