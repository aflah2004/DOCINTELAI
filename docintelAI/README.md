# DocIntel — AI-Powered Document Intelligence & Query System

Upload PDF documents, ask questions in plain English, and get answers
grounded in those documents — with the source page shown for every answer.

Runs locally with Flask, FAISS, Sentence Transformers and Ollama. No OpenAI/Anthropic API key is required.

---

## 1. Setup

### Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com) installed (free, local LLM runner)

### Steps

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install and start Ollama, then pull a small model (one-time)
ollama pull llama3.2

# 3. Run the app
python app.py
```

Open **http://localhost:5000** in your browser.

> If Ollama is not running, DocIntel will still retrieve and display the relevant source passages, but it will clearly label the AI answer as unavailable rather than pretending an excerpt is a generated answer.

The first run also downloads the embedding model
(`all-MiniLM-L6-v2`, ~80 MB) automatically via `sentence-transformers`.

---

## 2. Approach

The system follows a standard **Retrieval-Augmented Generation (RAG)**
pipeline:

```
PDF upload → extract text per page → split into overlapping chunks
   → embed chunks (sentence-transformers) → store in FAISS index

Question → embed question → retrieve top-k similar chunks (FAISS)
   → build a prompt with those chunks as context → local LLM (Ollama)
   → answer + cited sources shown to the user
```

Each answer is returned along with the **filename, page number, and
excerpt** of every chunk that was used to generate it, so the user can
verify where the answer came from.

### Why this dataset choice
No sample dataset was provided, so the app is designed to work with
**any PDF the user uploads** rather than shipping a fixed demo dataset —
that's closer to how the finished product would actually be used, and it
demonstrates the pipeline generalizes rather than being tuned to one file.

---

## 3. Key technical decisions

| Decision | Choice | Why |
|---|---|---|
| Embeddings | `sentence-transformers` (`all-MiniLM-L6-v2`), local | Free, no API key, small (~80MB), fast enough on CPU, good general-purpose quality for short passages |
| Vector store | FAISS (`IndexIDMap2` over `IndexFlatIP`) | Free, in-process, no server to run; `IndexIDMap2` lets individual documents be deleted later by removing their vector IDs, which a plain `IndexFlatL2` doesn't support |
| Answer generation | Ollama running a local model (default `llama3.2`) | Free/local requirement ruled out OpenAI/Anthropic APIs; Ollama is the standard way to run an instruction-tuned LLM locally with a simple HTTP API |
| Chunking | ~220 words per chunk, 50-word overlap, chunked per PDF page | Keeps chunks small enough for good retrieval precision while overlap prevents losing an answer that straddles a chunk boundary; per-page chunking keeps page-number citations accurate |
| Retrieval | Semantic similarity + lightweight keyword overlap, with up to 8 final context chunks |
| Similarity metric | Cosine similarity via L2-normalized vectors + inner product | Standard choice for sentence embeddings; simpler than maintaining a separate distance metric |
| Storage | SQLite (documents + chat history) + FAISS index on disk | Both survive an app restart; no external DB server needed for a local demo |
| Web framework | Flask, server-rendered pages + one JSON endpoint (`/api/ask`) | Matches the "application" requirement without the overhead of a JS framework; `/api/ask` uses `fetch` so the chat feels responsive without a full page reload |

### Graceful degradation (edge case handling)
- **No documents uploaded** → asking a question returns a clear message
  instead of erroring.
- **Question doesn't match anything** → a similarity threshold
  (`min_score`) filters out weak matches, so the app says it couldn't
  find relevant content instead of guessing from unrelated chunks.
- **Ollama not running / model not pulled** → relevant sources are still returned, but the response clearly explains that the local LLM is unavailable and gives the user the exact recovery steps.
- **Non-PDF file uploaded** → rejected with a flash message; nothing is
  silently ignored.
- **Deleting a document** → removes its vectors from FAISS (not just the
  database row), so it stops affecting future answers immediately.

---

## 4. Features
- Multi-page navigation: **Ask**, **Upload**, **Documents**, **History**, **Settings**
- Local sign up / log in / log out with hashed passwords
- Upload page is protected by authentication
- Light/normal and dark/night themes with localStorage persistence
- Help Center, Contact Us and administrator Contact Inbox
- Drag-and-drop multi-file PDF upload
- Delete individual documents (removes them from the search index too)
- Full question/answer history, with per-entry delete and "clear all"
- Every answer shows its source documents, page numbers, and excerpts

---

## 5. Limitations
- Local LLMs (via Ollama) are slower and somewhat less accurate than
  hosted frontier models — acceptable for a free/local demo, but a
  production system might offer a hosted-API mode as well.
- Retrieval is pure vector similarity; there's no keyword/BM25 hybrid
  search or re-ranking step, so very specific terms (exact codes, names)
  can occasionally be missed if their embedding isn't distinctive.
- Local authentication is intended for the machine-test/demo scope; production deployment would need CSRF protection, secure secrets, email verification and password recovery.
- Only PDF is supported (as scoped for this test); other formats would
  need their own text-extraction step.
- FAISS here runs as a simple in-memory/on-disk index; it isn't built to
  scale to millions of chunks.

## 6. Possible future improvements
- Add a hybrid search (BM25 + vector) with a re-ranking model for
  better precision on keyword-heavy queries.
- Stream the LLM's answer token-by-token instead of waiting for the
  full response.
- Support DOCX/TXT/HTML uploads in addition to PDF.
- Add a "confidence" indicator based on retrieval score, not just mode.
- Optional hosted-API mode (OpenAI/Anthropic) as a toggle for users who
  do have a key and want higher-quality answers.
- Package as a single Docker container (Flask + Ollama) for one-command
  setup.
