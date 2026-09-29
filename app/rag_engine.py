# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
"""Vector Search & Policy RAG Ingestion Pipeline (Altostrat Singapore Handbook)

Supports multilingual query expansion (Korean/English), robust semantic chunking,
and dense Cosine Similarity retrieval against ALTOSTRAT SINGAPORE EMPLOYEE POLICY HANDBOOK.
"""

import os
import re
import math
from typing import Any, Dict, List, Optional
from collections import Counter

POLICIES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "policies"
)

# Korean-English bilingual semantic expansion dictionary
BILINGUAL_SYNONYMS = {
    "육아휴직": ["maternity leave", "parental leave", "baby bonding leave", "spl"],
    "출산휴가": ["maternity leave", "parental leave", "24 weeks", "pregnancy"],
    "출산": ["maternity leave", "childbirth", "baby bonding"],
    "임신": ["pregnancy", "maternity leave"],
    "모성휴가": ["maternity leave", "24 weeks"],
    "육아": ["childcare leave", "baby bonding leave", "6 days"],
    "돌봄": ["carer's leave", "childcare leave", "8 weeks"],
    "병가": ["outpatient sick leave", "hospitalization leave", "14 days", "46 days", "medical certificate", "mc"],
    "진단서": ["medical certificate", "mc", "48 hours", "workweek"],
    "입원": ["hospitalization leave", "46 days"],
    "연차": ["vacation leave", "pto", "20 days", "21 days", "22 days", "workweek"],
    "휴가": ["vacation leave", "leave", "time off"],
    "식대": ["meal allowances", "120", "business travel", "concur"],
    "출장": ["travel expense", "t&e", "hotel", "flights", "concur"],
    "경비": ["expense submission", "concur", "company card", "non-reimbursable"],
    "회식": ["group meals", "senior colleague", "vp pre-approval", "500"],
    "경조사": ["bereavement leave", "4 weeks", "pet loss"],
    "반려동물": ["pet loss", "pet care", "non-reimbursable", "bereavement"],
    "강아지": ["pet loss", "pet care"],
    "고양이": ["pet loss", "pet care"],
    "재택": ["flexible work", "remote work", "home office"],
    "모니터": ["hardware procurement", "27-inch", "external display", "monitor"],
    "선물": ["gifts", "commercial gifts", "entertainment", "anti-bribery"],
    "뇌물": ["anti-bribery", "government ethics", "corruption"],
    "퇴사": ["exit policy", "notice period", "termination"],
}


class PolicyChunk:
    def __init__(
        self,
        document_name: str,
        section_title: str,
        section_number: str,
        content: str,
        citation: str,
        category: str,
    ):
        self.document_name = document_name
        self.section_title = section_title
        self.section_number = section_number
        self.content = content
        self.citation = citation
        self.category = category
        self.vector = self._compute_vector(f"{section_title} {content}")

    def _compute_vector(self, text: str) -> Dict[str, float]:
        tokens = re.findall(r"\b\w+\b", text.lower())
        counts = Counter(tokens)
        
        # Word n-grams for phrase grounding
        for i in range(len(tokens) - 1):
            ngram = f"{tokens[i]}_{tokens[i+1]}"
            counts[ngram] += 2.5
            
        for i in range(len(tokens) - 2):
            trigram = f"{tokens[i]}_{tokens[i+1]}_{tokens[i+2]}"
            counts[trigram] += 1.5

        total = math.sqrt(sum(v * v for v in counts.values())) or 1.0
        return {k: v / total for k, v in counts.items()}


class VectorPolicyStore:
    """In-Memory Vector Search Datastore for Altostrat Singapore Handbook."""

    def __init__(self, docs_dir: str = POLICIES_DIR):
        self.docs_dir = docs_dir
        self.chunks: List[PolicyChunk] = []
        self._index_documents()

    def _index_documents(self):
        """Scans docs/policies/*.md and ingests chunks into vector index."""
        if not os.path.exists(self.docs_dir):
            return

        for fname in sorted(os.listdir(self.docs_dir)):
            if not fname.endswith(".md"):
                continue

            file_path = os.path.join(self.docs_dir, fname)
            doc_display_name = "ALTOSTRAT_SG_Handbook.pdf" if "altostrat" in fname.lower() else fname.replace(".md", ".pdf")
            
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()

            raw_blocks = re.split(r"(\*\*(?:SECTION\s+\d+:[^*]+|\d+\.\d+\s+[^*]+)\*\*|###\s+Section\s+[\d\.]+:[^\n]+)", raw_text)
            
            if len(raw_blocks) > 1:
                for i in range(1, len(raw_blocks), 2):
                    header = raw_blocks[i].strip("*# \n")
                    body = raw_blocks[i + 1].strip() if i + 1 < len(raw_blocks) else ""
                    
                    sec_match = re.search(r"(?:Section\s+)?(\d+\.\d+|\d+)", header, re.IGNORECASE)
                    sec_num = sec_match.group(1) if sec_match else "1.0"
                    citation = f"{doc_display_name} §{sec_num}"

                    header_lower = header.lower()
                    if any(k in header_lower for k in ["leave", "sick", "vacation", "maternity", "bonding", "childcare", "bereavement"]):
                        category = "Leave"
                    elif any(k in header_lower for k in ["travel", "expense", "card", "meal", "lodging"]):
                        category = "Expenses"
                    elif any(k in header_lower for k in ["flexible", "remote", "hybrid"]):
                        category = "RemoteWork"
                    elif any(k in header_lower for k in ["conduct", "ethics", "bribery", "gift", "harassment"]):
                        category = "CodeOfConduct"
                    else:
                        category = "General"

                    chunk = PolicyChunk(
                        document_name=doc_display_name,
                        section_title=header,
                        section_number=sec_num,
                        content=body,
                        citation=citation,
                        category=category,
                    )
                    self.chunks.append(chunk)

    def _expand_query(self, query: str) -> str:
        """Expands query with multilingual synonyms for robust Korean/English matching."""
        expanded = [query]
        for kr_term, en_synonyms in BILINGUAL_SYNONYMS.items():
            if kr_term in query:
                expanded.extend(en_synonyms)
        return " ".join(expanded)

    def search(
        self, query: str, category: Optional[str] = None, top_k: int = 3, min_score: float = 0.06
    ) -> Dict[str, Any]:
        """Performs bilingual cosine vector similarity search against ingested policy chunks."""
        full_query = self._expand_query(query)
        stop_words = {"what", "is", "the", "company", "policy", "regarding", "to", "a", "an", "and", "in", "of", "for", "on", "with", "about", "are", "do", "i", "can", "our", "under"}
        raw_tokens = re.findall(r"\b\w+\b", full_query.lower())
        query_tokens = [t for t in raw_tokens if t not in stop_words] or raw_tokens
        if not query_tokens:
            return {"status": "NOT_FOUND", "grounded": False, "results": []}

        q_counts = Counter(query_tokens)
        for i in range(len(query_tokens) - 1):
            q_counts[f"{query_tokens[i]}_{query_tokens[i+1]}"] += 2.5
        for i in range(len(query_tokens) - 2):
            q_counts[f"{query_tokens[i]}_{query_tokens[i+1]}_{query_tokens[i+2]}"] += 1.5

        q_total = math.sqrt(sum(v * v for v in q_counts.values())) or 1.0
        q_vec = {k: v / q_total for k, v in q_counts.items()}

        def _do_search(cat_filter: Optional[str]):
            scored = []
            for chunk in self.chunks:
                if cat_filter and cat_filter != "General" and chunk.category != cat_filter:
                    continue
                dot_product = sum(
                    q_val * chunk.vector.get(k, 0.0) for k, q_val in q_vec.items()
                )
                if dot_product >= min_score:
                    scored.append((dot_product, chunk))
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored

        matches = _do_search(category)
        if not matches and category and category != "General":
            matches = _do_search(None)

        top_matches = matches[:top_k]

        if not top_matches:
            return {
                "status": "NOT_FOUND",
                "grounded": False,
                "message": "No official policy matching your query was found in the Altostrat Singapore Handbook.",
                "citation": None,
                "results": [],
            }

        results = []
        for score, chunk in top_matches:
            is_disallowed = any(w in chunk.content.lower() for w in ["non-reimbursable", "strictly prohibited", "prohibited", "disallowed", "forfeited"])
            results.append({
                "document": chunk.document_name,
                "section": chunk.section_title,
                "content": chunk.content,
                "citation": chunk.citation,
                "similarity_score": round(score, 4),
                "is_disallowed": is_disallowed,
            })

        return {
            "status": "SUCCESS",
            "grounded": True,
            "results": results,
            "primary_citation": results[0]["citation"],
            "top_similarity": results[0]["similarity_score"],
        }


# Global Singleton Vector Datastore instance
policy_vector_store = VectorPolicyStore()
