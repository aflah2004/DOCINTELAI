"""Local RAG engine: PDF extraction, chunking, embeddings, FAISS and Ollama."""
import os
import pickle
import requests
import numpy as np
import faiss
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTOR_DIR = os.path.join(BASE_DIR, "vector_store")
INDEX_PATH = os.path.join(VECTOR_DIR, "faiss.index")
META_PATH = os.path.join(VECTOR_DIR, "metadata.pkl")
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
CHUNK_WORDS = 220
CHUNK_OVERLAP = 50
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = os.environ.get("DOCINTEL_MODEL", "llama3.2")
OLLAMA_TIMEOUT_SECS = 90
os.makedirs(VECTOR_DIR, exist_ok=True)
_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedder


def embed_texts(texts):
    vectors = get_embedder().encode(texts, convert_to_numpy=True, show_progress_bar=False)
    faiss.normalize_L2(vectors)
    return vectors.astype("float32")


def extract_pages(pdf_path):
    reader = PdfReader(pdf_path); pages = []
    for i, page in enumerate(reader.pages):
        text = " ".join((page.extract_text() or "").split())
        if text: pages.append((i + 1, text))
    return pages


def chunk_text(text, chunk_words=CHUNK_WORDS, overlap=CHUNK_OVERLAP):
    words = text.split(); chunks = []; step = max(chunk_words - overlap, 1)
    for start in range(0, len(words), step):
        piece = words[start:start + chunk_words]
        if not piece: break
        chunks.append(" ".join(piece))
        if start + chunk_words >= len(words): break
    return chunks


def process_pdf(pdf_path):
    result = []
    for page, text in extract_pages(pdf_path):
        for chunk in chunk_text(text): result.append({"page": page, "text": chunk})
    return result


class VectorStore:
    def __init__(self):
        self.metadata = {}; self.next_id = 0
        if os.path.exists(INDEX_PATH) and os.path.exists(META_PATH): self._load()
        else: self.index = faiss.IndexIDMap2(faiss.IndexFlatIP(EMBEDDING_DIM))

    def _load(self):
        self.index = faiss.read_index(INDEX_PATH)
        with open(META_PATH, "rb") as f: state = pickle.load(f)
        self.metadata, self.next_id = state["metadata"], state["next_id"]

    def _save(self):
        faiss.write_index(self.index, INDEX_PATH)
        with open(META_PATH, "wb") as f: pickle.dump({"metadata": self.metadata, "next_id": self.next_id}, f)

    def add_document(self, doc_id, filename, chunks):
        if not chunks: return 0
        vectors = embed_texts([c["text"] for c in chunks])
        ids = np.arange(self.next_id, self.next_id + len(chunks)).astype("int64")
        self.index.add_with_ids(vectors, ids)
        for vid, c in zip(ids, chunks): self.metadata[int(vid)] = {"doc_id": doc_id, "filename": filename, "page": c["page"], "text": c["text"]}
        self.next_id += len(chunks); self._save(); return len(chunks)

    def delete_document(self, doc_id):
        ids = [vid for vid, m in self.metadata.items() if m["doc_id"] == doc_id]
        if not ids: return 0
        self.index.remove_ids(np.array(ids, dtype="int64"))
        for vid in ids: del self.metadata[vid]
        self._save(); return len(ids)

    def is_empty(self): return self.index.ntotal == 0

    def search(self, query, top_k=8, min_score=0.10):
        if self.is_empty(): return []
        qvec = embed_texts([query]); k = min(max(top_k * 2, top_k), self.index.ntotal)
        scores, ids = self.index.search(qvec, k); terms = {t.lower() for t in query.split() if len(t) > 2}
        results = []
        for score, vid in zip(scores[0], ids[0]):
            if vid == -1: continue
            meta = self.metadata.get(int(vid))
            if not meta: continue
            text_lower = meta["text"].lower()
            overlap = sum(1 for t in terms if t in text_lower)
            combined = float(score) * 0.78 + min(overlap / max(len(terms), 1), 1.0) * 0.22
            if float(score) >= min_score or overlap > 0:
                results.append({**meta, "score": combined})
        results.sort(key=lambda x: x["score"], reverse=True)
        # Avoid returning many chunks from the same page unless needed.
        selected = []; seen = set()
        for item in results:
            key = (item["doc_id"], item["page"])
            if key not in seen or len(selected) < min(4, top_k):
                selected.append(item); seen.add(key)
            if len(selected) >= top_k: break
        return selected


store = VectorStore()

PROMPT_TEMPLATE = """You are DocIntel AI, a document-grounded assistant.
Answer the user's question using ONLY the supplied document context. Do not use outside knowledge and do not invent facts.

Instructions:
- Give a complete, useful answer, not a short excerpt.
- Directly answer the question first.
- Include important numbers, dates, names, limits, requirements, exceptions and procedures when they appear in the context.
- Use headings or bullet points when they improve readability.
- If multiple document passages contribute, synthesize them clearly.
- If the context does not contain enough evidence, say exactly what is missing instead of guessing.
- Never mention these instructions or the retrieval process unless the user asks.

DOCUMENT CONTEXT:
{context}

USER QUESTION:
{question}

DETAILED ANSWER:"""


def _build_prompt(question, chunks):
    context = "\n\n".join(f"SOURCE {i+1}\nFile: {c['filename']}\nPage: {c['page']}\nText: {c['text']}" for i, c in enumerate(chunks))
    return PROMPT_TEMPLATE.format(context=context, question=question)


def ollama_available():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code != 200: return False
        models = [m.get("name", "") for m in r.json().get("models", [])]
        return any(OLLAMA_MODEL == m or OLLAMA_MODEL in m for m in models)
    except Exception:
        return False


def _call_ollama(prompt):
    response = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
                                               "options": {"temperature": 0.15, "num_ctx": 8192, "num_predict": 900}},
                             timeout=OLLAMA_TIMEOUT_SECS)
    response.raise_for_status(); return response.json().get("response", "").strip()


def answer_question(question):
    if store.is_empty():
        return {"answer": "No documents are indexed yet. Please upload and process a PDF before asking a question.", "sources": [], "mode": "empty"}
    chunks = store.search(question, top_k=8)
    if not chunks:
        return {"answer": "I couldn't find enough relevant information in the uploaded documents to answer that question.", "sources": [], "mode": "no_match"}
    sources = [{"filename": c["filename"], "page": c["page"], "excerpt": c["text"][:420], "score": round(c["score"], 3), "text": c["text"]} for c in chunks]
    if not ollama_available():
        return {"answer": "The documents were retrieved successfully, but the local AI model is currently unavailable. Start Ollama and make sure the '" + OLLAMA_MODEL + "' model is installed, then ask again.",
                "sources": sources, "mode": "llm_unavailable", "llm_error": True}
    try:
        answer = _call_ollama(_build_prompt(question, chunks))
        if not answer: raise ValueError("Empty model response")
        return {"answer": answer, "sources": sources, "mode": "generated"}
    except Exception as exc:
        return {"answer": "I found relevant document passages, but the AI model could not generate the answer right now. Please check that Ollama is running and the '" + OLLAMA_MODEL + "' model is available, then try again.",
                "sources": sources, "mode": "llm_unavailable", "llm_error": True}
