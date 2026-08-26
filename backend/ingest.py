"""
Loads the 6 source PDFs into ChromaDB, chunked, with authority metadata
attached to every chunk. This metadata is what lets the agent reason
about source precedence instead of treating every chunk as equally
trustworthy.

Authority levels (lower number = higher authority):
  1 = customer-specific signed agreement (overrides everything for that account)
  2 = current SOP
  3 = current policy
  4 = product ops guide / known issues (factual reference, not a policy)
  5 = deprecated policy (retained for history only — must not be used to answer)
"""

import re
import pickle
from pathlib import Path
import chromadb
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np

DATA_DIR = Path(__file__).parent / "data"
CHROMA_DIR = Path(__file__).parent / "chroma_store"
VECTORIZER_PATH = Path(__file__).parent / "tfidf_vectorizer.pkl"

# Query expansion: TF-IDF matches vocabulary exactly, so a paraphrased
# question ("is there a charge to cancel") can miss a chunk that says
# "cancellation fee" even though they mean the same thing. This maps
# common paraphrases in the support-ops domain onto the document's actual
# vocabulary before embedding the query. Cheap, explainable mitigation —
# a real embedding model would need this less, but it's free and it works
# for the domain terms this system actually sees.
QUERY_SYNONYMS = {
    "charge": "fee cancellation fee",
    "cost": "fee",
    "refund": "credit service credit",
    "compensation": "credit service credit",
    "late": "delay past window overdue",
    "overdue": "delay past window",
    "cancel": "cancellation cancel",
    "cancelled": "cancellation cancel",
    "canceling": "cancellation cancel",
    "outage": "P1 critical production outage",
    "down": "outage P1 critical",
    "hacked": "security credential exposure breach",
    "leaked": "security credential exposure breach",
    "response time": "first-response target SLA",
    "sla": "first-response target severity",
    "picked up": "PICKED_UP pickup",
    "not picked up": "BOOKED pickup",
    "booked": "BOOKED",
}


def expand_query(query: str) -> str:
    q = query.lower()
    extra_terms = []
    for phrase, expansion in QUERY_SYNONYMS.items():
        if phrase in q:
            extra_terms.append(expansion)
    if extra_terms:
        return query + " " + " ".join(extra_terms)
    return query


class TfidfEmbeddingFunction(chromadb.EmbeddingFunction):
    """Offline embedding function (no external model download / API call
    needed). Fit once at ingestion time on the corpus, reused at query
    time. Swappable for OpenAI/sentence-transformer embeddings in
    production — see architecture note."""

    def __init__(self, vectorizer: TfidfVectorizer | None = None):
        self.vectorizer = vectorizer

    def __call__(self, input: list[str]) -> list[list[float]]:
        vecs = self.vectorizer.transform(input)
        return vecs.toarray().tolist()

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self(input)

    def name(self) -> str:
        return "tfidf_offline"

DOC_REGISTRY = [
    {
        "file": "01_Support_Policy_v3_CURRENT.pdf",
        "doc_type": "policy",
        "authority_level": 3,
        "status": "current",
        "account_id": None,  # None = applies to all accounts unless overridden
        "title": "Support Policy v3 (current)",
    },
    {
        "file": "02_Support_Policy_v2_DEPRECATED.pdf",
        "doc_type": "policy",
        "authority_level": 5,
        "status": "deprecated",
        "account_id": None,
        "title": "Support Policy v2 (DEPRECATED — superseded by v3)",
    },
    {
        "file": "03_Cancellation_and_Service_Credit_SOP_v4.pdf",
        "doc_type": "sop",
        "authority_level": 2,
        "status": "current",
        "account_id": None,
        "title": "Cancellation & Service Credit SOP v4 (current)",
    },
    {
        "file": "04_Product_Operations_Guide_and_Known_Issues.pdf",
        "doc_type": "product_guide",
        "authority_level": 4,
        "status": "current",
        "account_id": None,
        "title": "Product Operations Guide & Known Issues",
    },
    {
        "file": "05_Northstar_Logistics_Enterprise_Agreement.pdf",
        "doc_type": "agreement",
        "authority_level": 1,
        "status": "current",
        "account_id": "ACCT-001",
        "title": "Northstar Logistics Enterprise Agreement",
    },
    {
        "file": "06_LumenWorks_Service_Agreement.pdf",
        "doc_type": "agreement",
        "authority_level": 1,
        "status": "current",
        "account_id": "ACCT-002",
        "title": "LumenWorks Service Agreement",
    },
]


def load_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def chunk_by_section(text: str, min_len: int = 40) -> list[str]:
    """Splits on numbered headings (e.g. '1. Order cancellation') and
    falls back to paragraph splitting. Keeps each clause intact rather
    than chunking by raw character count, since a policy is easiest to
    reason over when a clause isn't cut in half."""
    # split on lines that look like "1. Heading" or "2 Heading"
    pattern = r"\n(?=\d{1,2}\.\s+[A-Z])"
    parts = re.split(pattern, text)
    chunks = []
    for p in parts:
        p = p.strip()
        if len(p) >= min_len:
            chunks.append(p)
    if not chunks:  # fallback: paragraph split
        chunks = [c.strip() for c in text.split("\n\n") if len(c.strip()) >= min_len]
    return chunks


def build_vector_store():
    CHROMA_DIR.mkdir(exist_ok=True)

    ids, docs, metadatas = [], [], []
    for entry in DOC_REGISTRY:
        path = DATA_DIR / entry["file"]
        text = load_pdf_text(path)
        chunks = chunk_by_section(text)
        for i, chunk in enumerate(chunks):
            ids.append(f"{entry['file']}::chunk{i}")
            docs.append(chunk)
            metadatas.append({
                "source_file": entry["file"],
                "title": entry["title"],
                "doc_type": entry["doc_type"],
                "authority_level": entry["authority_level"],
                "status": entry["status"],
                "account_id": entry["account_id"] or "ALL",
            })

    # fit TF-IDF on the full chunk corpus, persist it so the query-time
    # embedding function uses the exact same vocabulary/weights
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2))
    vectorizer.fit(docs)
    with open(VECTORIZER_PATH, "wb") as f:
        pickle.dump(vectorizer, f)

    ef = TfidfEmbeddingFunction(vectorizer)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    try:
        client.delete_collection("parcelpilot_docs")
    except Exception:
        pass
    collection = client.create_collection(
        "parcelpilot_docs", embedding_function=ef, metadata={"hnsw:space": "cosine"}
    )
    collection.add(ids=ids, documents=docs, metadatas=metadatas)
    print(f"Ingested {len(ids)} chunks from {len(DOC_REGISTRY)} documents.")
    return collection


def load_vector_store():
    """Used at query time by tools.py — loads the persisted collection
    with the same fitted vectorizer."""
    with open(VECTORIZER_PATH, "rb") as f:
        vectorizer = pickle.load(f)
    ef = TfidfEmbeddingFunction(vectorizer)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_collection("parcelpilot_docs", embedding_function=ef)


def assess_confidence(results: list[dict], account_id: str | None = None) -> dict:
    """
    Give an explainable confidence signal for retrieved evidence.

    Account-specific agreements only participate in authority/conflict
    checks when they actually apply to the requested account.

    Global sources (account_id == "ALL") apply to every account.
    """

    if not results:
        return {
            "confidence": "low",
            "reason": "No matching source found.",
        }

    # Only use sources that are actually applicable to the account.
    applicable = [
        r for r in results
        if (
            not account_id
            or r.get("applies_to_account") in (None, "", "ALL", account_id)
        )
    ]

    if not applicable:
        return {
            "confidence": "low",
            "reason": "Retrieved sources are not applicable to this account.",
        }

    # Results are already ordered by retrieval relevance.
    top = applicable[0]

    if top.get("relevance_score", 0) < 0.2:
        return {
            "confidence": "low",
            "reason": (
                "Best applicable match has very low relevance — "
                "likely no documented answer."
            ),
        }

    # Deprecated sources must never increase confidence.
    if top.get("status") == "deprecated":
        return {
            "confidence": "low",
            "reason": (
                "Best applicable match is a deprecated source — "
                "do not use it to answer."
            ),
        }

    # A higher-authority applicable source should trigger a hierarchy check.
    higher_authority_elsewhere = [
        r
        for r in applicable[1:]
        if (
            r.get("authority_level") is not None
            and top.get("authority_level") is not None
            and r["authority_level"] < top["authority_level"]
            and r.get("relevance_score", 0) > 0.08
        )
    ]

    if higher_authority_elsewhere:
        other = higher_authority_elsewhere[0]

        return {
            "confidence": "medium",
            "reason": (
                f"Top text match is {top['source']} "
                f"(authority level {top['authority_level']}), "
                f"but a higher-authority applicable source is also relevant: "
                f"{other['source']} "
                f"(authority level {other['authority_level']}). "
                "Check the higher-authority source first — it overrides "
                "if applicable to this account."
            ),
        }

    if len(applicable) > 1:
        second = applicable[1]

        close_relevance = (
            abs(
                top.get("relevance_score", 0)
                - second.get("relevance_score", 0)
            ) < 0.1
        )

        different_authority = (
            top.get("authority_level") != second.get("authority_level")
        )

        if close_relevance and different_authority:
            return {
                "confidence": "medium",
                "reason": (
                    "Top applicable matches come from sources with "
                    "different authority levels "
                    f"({top['source']} [level {top['authority_level']}] vs "
                    f"{second['source']} [level {second['authority_level']}]) — "
                    "apply the authority hierarchy explicitly."
                ),
            }

    return {
        "confidence": "high",
        "reason": (
            f"Clear applicable match from {top['source']} "
            f"(authority level {top['authority_level']})."
        ),
    }


if __name__ == "__main__":
    col = build_vector_store()
    # sanity check retrieval
    res = col.query(query_texts=["cancellation fee for Northstar"], n_results=3)
    for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
        print("---")
        print(meta["title"], "| authority:", meta["authority_level"], "| status:", meta["status"])
        print(doc[:150].replace("\n", " "))

    print("\n=== paraphrase test (query expansion) ===")
    for q in ["is there a charge to cancel", "was there a data breach", "how late can pickup be"]:
        res2 = col.query(query_texts=[expand_query(q)], n_results=1)
        print(q, "->", res2["metadatas"][0][0]["title"])
