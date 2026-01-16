import json
from typing import Any, Dict, Optional

from app.settings import settings


class LLMService:
    """Minimal LLM wrapper.

    - Uses OpenAI if OPENAI_API_KEY is configured.
    - Otherwise returns None so callers can fall back to heuristics.
    """

    def __init__(self):
        self.api_key = (settings.openai_api_key or "").strip()
        self.model = (settings.openai_model or "gpt-4o-mini").strip()

    def enabled(self) -> bool:
        return bool(self.api_key)

    def _client(self):
        # Imported lazily so the project can run even if 'openai' isn't installed.
        from openai import OpenAI  # type: ignore
        return OpenAI(api_key=self.api_key)

    def extract_fulltext_query(self, issue_text: str) -> Optional[Dict[str, Any]]:
        """Return {query, terms, confidence, rationale} or None."""
        if not self.enabled():
            return None

        system = (
            "You are a software analyst helping build Neo4j code search queries. "
            "Given a defect/user story, create a Neo4j fulltext query string that "
            "maximizes matches to code identifiers (class names, method names, fields), "
            "domain nouns, and likely DTO/response terms. "
            "Output STRICT JSON only."  # important for parsing
        )

        user = {
            "issue": issue_text,
            "instructions": {
                "output_schema": {
                    "query": "string (use OR, quotes for phrases; no cypher)",
                    "terms": "array of strings (key tokens/identifiers)",
                    "confidence": "number 0..1", 
                    "rationale": "short string"
                },
                "query_guidelines": [
                    "Prefer 8-18 key terms.",
                    "Include synonyms (org/organization/company/tenant/account) if relevant.",
                    "Include response/dto/mapper/service/repository keywords when relevant.",
                    "Avoid stopwords."
                ]
            }
        }

        client = self._client()
        resp = client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)}
            ],
        )
        content = resp.choices[0].message.content or ""

        try:
            data = json.loads(content)
            if isinstance(data, dict) and isinstance(data.get("query"), str):
                return data
        except Exception:
            # If the model didn't output JSON, fail closed and let caller fall back.
            return None

        return None

    def extract_cypher_hints(self, issue_text: str) -> Optional[Dict[str, Any]]:
        """Return hints to form non-fulltext Cypher.

        Expected output JSON:
        {
          "identifiers": ["UserService", "OrganizationName", ...],
          "keywords": ["organization", "dto", "mapper", ...],
          "entity_types": ["Type", "Method", "Field"],
          "confidence": 0..1,
          "rationale": "..."
        }
        """
        if not self.enabled():
            return None

        system = (
            "You help convert issue text into query hints over a Java code property graph in Neo4j. "
            "Return STRICT JSON only. Identify likely class/method/field identifiers and keywords."
        )
        user = {
            "issue": issue_text,
            "schema": {
                "identifiers": "array of strings (CamelCase identifiers if present or likely)",
                "keywords": "array of strings (domain terms/synonyms)",
                "entity_types": "array subset of [Type, Method, Field]",
                "confidence": "number 0..1",
                "rationale": "short string"
            },
            "notes": [
                "If the issue mentions missing fields in responses, include dto/response/mapper/assembler.",
                "If it mentions wrong data, include service/repository/client/cache.",
                "Add org synonyms (org, organization, company, tenant, account) when appropriate."
            ]
        }

        client = self._client()
        resp = client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)}
            ],
        )
        content = resp.choices[0].message.content or ""
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                return data
        except Exception:
            return None
        return None
