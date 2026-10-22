from __future__ import annotations

import itertools
import math

import pytest

from app.rag.chunking import chunk_text
from app.rag.embeddings import HashingEmbedder
from app.rag.loaders import parse_upload, safe_filename
from tests.conftest import chat, make_pdf, register, upload


# --- chunking ---------------------------------------------------------------------
def test_chunks_respect_size_and_overlap():
    text = " ".join(f"Sentence number {i} talks about retrieval quality." for i in range(200))
    chunks = chunk_text(text, chunk_size=300, overlap=60)
    assert len(chunks) > 5
    assert all(len(c.text) <= 300 for c in chunks)
    for previous, current in itertools.pairwise(chunks):
        assert current.start < previous.end  # consecutive chunks overlap
        assert previous.end - current.start <= 60


def test_chunking_prefers_paragraph_breaks():
    text = ("Alpha paragraph. " * 10).strip() + "\n\n" + ("Beta paragraph. " * 10).strip()
    chunks = chunk_text(text, chunk_size=250, overlap=0)
    assert chunks[0].text.endswith("Alpha paragraph.")


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (100, 100), (100, -1)])
def test_chunking_rejects_bad_parameters(size, overlap):
    with pytest.raises(ValueError, match=r"chunk_size|overlap"):
        chunk_text("text", chunk_size=size, overlap=overlap)


def test_overlap_must_be_smaller_than_chunk_size_in_settings(tmp_path):
    from tests.conftest import make_settings

    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        make_settings(tmp_path, chunk_size=200, chunk_overlap=200)


# --- embeddings -----------------------------------------------------------------------
def test_hash_embeddings_are_deterministic_and_normalised():
    embedder = HashingEmbedder(256)
    first, second = (
        embedder.embed(["incident escalation policy"]),
        embedder.embed(["incident escalation policy"]),
    )
    assert first == second
    assert math.isclose(math.sqrt(sum(v * v for v in first[0])), 1.0, rel_tol=1e-9)
    assert len(first[0]) == 256


def test_hash_embeddings_reflect_lexical_similarity():
    embedder = HashingEmbedder(384)
    query, related, unrelated = embedder.embed(
        ["vector store latency", "latency of the vector store", "holiday party catering menu"]
    )

    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, related) > cosine(query, unrelated)


# --- loaders ------------------------------------------------------------------------------
def test_pdf_text_is_extracted():
    parsed = parse_upload("runbook.pdf", make_pdf("Rotate signing keys every ninety days"), max_bytes=10_000)
    assert parsed.content_type == "pdf"
    assert "Rotate signing keys" in parsed.text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.md", "passwd.md"),
        ("C:\\Users\\me\\notes.txt", "notes.txt"),
        ("report<script>.md", "report_script_.md"),
        ("...", "upload"),
    ],
)
def test_filenames_are_sanitised(raw, expected):
    assert safe_filename(raw) == expected


# --- upload API -----------------------------------------------------------------------------
def test_upload_txt_md_pdf_and_list(client):
    admin = register(client, "acme")
    upload(client, admin, "notes.txt", b"Our on-call rotation changes every Monday at 09:00 UTC.")
    upload(client, admin, "guide.md", b"# Guide\n\nCanary releases receive five percent of traffic.")
    upload(client, admin, "keys.pdf", make_pdf("Signing keys rotate every ninety days"))
    names = {d["filename"] for d in client.get("/documents", headers=admin.headers).json()}
    assert names == {"notes.txt", "guide.md", "keys.pdf"}


def test_unsupported_type_rejected(client):
    admin = register(client, "acme")
    response = client.post("/documents", files={"file": ("payload.exe", b"MZ...")}, headers=admin.headers)
    assert response.status_code == 415
    assert "Unsupported file type" in response.json()["error"]["message"]


def test_fake_pdf_and_binary_text_rejected(client):
    admin = register(client, "acme")
    fake_pdf = client.post(
        "/documents", files={"file": ("x.pdf", b"not really a pdf")}, headers=admin.headers
    )
    binary_txt = client.post(
        "/documents", files={"file": ("x.txt", b"\x00\x01\x02binary")}, headers=admin.headers
    )
    assert fake_pdf.status_code == 415
    assert binary_txt.status_code == 415


def test_oversized_and_empty_uploads_rejected(app_factory):
    client = app_factory(max_upload_mb=0.001)  # ~1 KB
    admin = register(client, "acme")
    too_big = client.post("/documents", files={"file": ("big.txt", b"a" * 5000)}, headers=admin.headers)
    empty = client.post("/documents", files={"file": ("empty.txt", b"")}, headers=admin.headers)
    whitespace = client.post("/documents", files={"file": ("blank.md", b"   \n\n  ")}, headers=admin.headers)
    assert too_big.status_code == 413
    assert empty.status_code == 422
    assert whitespace.status_code == 422


def test_duplicate_upload_rejected(client):
    admin = register(client, "acme")
    upload(client, admin, "a.txt", b"Backups run nightly at 02:00 UTC.")
    again = client.post(
        "/documents",
        files={"file": ("copy.txt", b"Backups run nightly at 02:00 UTC.")},
        headers=admin.headers,
    )
    assert again.status_code == 409


def test_uploaded_document_is_searchable_and_cited(client):
    admin = register(client, "acme")
    upload(
        client,
        admin,
        "backups.md",
        b"# Backups\n\nDatabase backups run nightly at 02:00 UTC and are kept "
        b"for 35 days in a separate account.",
    )
    run = chat(client, admin, "How long are database backups kept?")
    assert "35 days" in run["response"]
    assert run["citations"][0]["filename"] == "backups.md"


def test_deleted_document_is_no_longer_retrieved(client):
    admin = register(client, "acme")
    doc = upload(client, admin, "temp.md", b"The staging password rotation happens on Fridays at noon.")
    assert client.delete(f"/documents/{doc['id']}", headers=admin.headers).status_code == 204
    hits = client.get(
        "/documents/search", params={"q": "staging password rotation"}, headers=admin.headers
    ).json()["results"]
    assert hits == []


def test_standard_user_cannot_delete_documents(client):
    admin = register(client, "acme")
    from tests.conftest import add_user

    user = add_user(client, admin, "analyst@acme.example")
    doc = upload(client, admin, "keep.md", b"Keep this document available to everyone in the team.")
    assert client.delete(f"/documents/{doc['id']}", headers=user.headers).status_code == 403


def test_light_stemming_matches_word_variants():
    from app.rag.embeddings import tokenize

    assert len(set(tokenize("approve approval approved"))) == 1
    assert tokenize("tools") == tokenize("tool")
    assert tokenize("state-changing") == [*tokenize("state"), *tokenize("changing")]


def test_paraphrased_question_retrieves_the_right_policy(client):
    from tests.conftest import upload_sample

    admin = register(client, "acme")
    upload_sample(client, admin, "acme")
    run = chat(client, admin, "Who can approve state-changing tools?")
    assert run["citations"][0]["filename"] == "security-policy.md"
    assert "requires approval from an administrator" in run["response"]
