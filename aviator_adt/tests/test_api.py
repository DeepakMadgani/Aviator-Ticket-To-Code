from unittest.mock import AsyncMock


def test_docs(client):
    response = client.get("/docs")

    assert response.status_code == 200


def test_direct_chat(client):
    """Test the /v1/direct-chat endpoint."""
    payload = {"messages": [{"author": "user", "content": "Say hello in one word"}]}

    response = client.post("/v1/direct-chat", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    assert isinstance(data["result"], str)
    assert len(data["result"]) > 0


def test_direct_chat_with_chat_id(client):
    """Test the /v1/direct-chat endpoint with chatID."""
    payload = {"messages": [{"author": "user", "content": "What is 2+2?"}], "chatID": "test-session-123"}

    response = client.post("/v1/direct-chat", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert "result" in data
    assert data.get("chatID") == "test-session-123"


def test_direct_chat_missing_messages(client):
    """Test the /v1/direct-chat endpoint without messages."""
    payload = {}

    response = client.post("/v1/direct-chat", json=payload)

    # Should return 422 for validation error (missing required field)
    assert response.status_code == 422


def test_chat_replaces_chunk_ids_in_result(client, monkeypatch):
    """Test the /v1/chat endpoint rewrites inline chunk IDs to citation numbers."""
    from langchain.messages import AIMessage

    from aviator import models
    from aviator.api import v1

    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "messages": [
            AIMessage(
                content=(
                    "There are incidents in Alaska "
                    "[019cdb48-0292-7f90-ae82-3ee440d924db] and Anchorage "
                    "[019cdb2d-8d8c-7540-9f18-a70596669f13]."
                )
            )
        ],
        "references": [
            models.ReferenceModel(
                document_id="doc-1",
                workspace_id="ws-1",
                chunks=[
                    models.ReferenceChunkModel(
                        chunk_id="019cdb48-0292-7f90-ae82-3ee440d924db",
                        citation=1,
                        content="Alaska incident",
                        source="RAG",
                    )
                ],
            ),
            models.ReferenceModel(
                document_id="doc-2",
                workspace_id="ws-1",
                chunks=[
                    models.ReferenceChunkModel(
                        chunk_id="019cdb2d-8d8c-7540-9f18-a70596669f13",
                        citation=2,
                        content="Anchorage incident",
                        source="RAG",
                    )
                ],
            ),
        ],
    }

    monkeypatch.setattr(v1.aviator, "get_graph", AsyncMock(return_value=mock_graph))

    payload = {
        "messages": [{"author": "user", "content": "Any incidents in Alaska?"}],
        "inlineCitation": True,
    }

    response = client.post("/v1/chat", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["result"] == "There are incidents in Alaska [1] and Anchorage [2]."
    assert data["references"][0]["chunks"][0]["chunkID"] == "019cdb48-0292-7f90-ae82-3ee440d924db"
    assert data["references"][0]["chunks"][0]["citation"] == 1


def test_chat_response_model_with_query_metadata():
    """Test ChatResponseModel includes query_metadata field."""
    from aviator import models

    query_metadata = models.QueryMetadataModel(originalQuery="What is AI?", usedQuery="artificial intelligence")
    response = models.ChatResponseModel(
        result="AI is...",
        context="{}",
        queryMetadata=query_metadata,  # Use alias since model doesn't have populate_by_name
    )

    assert response.queryMetadata is not None
    assert response.queryMetadata.originalQuery == "What is AI?"
    assert response.queryMetadata.usedQuery == "artificial intelligence"


def test_reference_model_with_distance_field():
    """Test that ReferenceModel includes distance field."""
    from aviator import models

    chunk = models.ReferenceChunkModel(chunk_id="chunk-1", content="Test chunk content", distance=0.92)
    reference = models.ReferenceModel(document_id="doc-123", workspace_id="ws-456", distance=0.92, chunks=[chunk])

    assert reference.distance == 0.92
    assert reference.chunks[0].distance == 0.92


def test_reference_chunk_model_optional_citation_and_distance():
    """Test that ReferenceChunkModel has optional citation and distance fields."""
    from aviator import models

    # With all fields
    chunk_full = models.ReferenceChunkModel(chunk_id="chunk-1", citation=1, content="Content", distance=0.85)
    assert chunk_full.citation == 1
    assert chunk_full.distance == 0.85

    # Without citation and distance (both optional now)
    chunk_minimal = models.ReferenceChunkModel(chunk_id="chunk-2", content="Content")
    assert chunk_minimal.citation is None
    assert chunk_minimal.distance is None


def test_reference_model_metadata_backwards_compatibility():
    """Test that ReferenceModel.metadata includes backwards compatibility fields."""
    from aviator import models

    chunks = [
        models.ReferenceChunkModel(chunk_id="chunk-1", content="First chunk", source="RAG", distance=0.90),
        models.ReferenceChunkModel(chunk_id="chunk-2", content="Second chunk", source="RAG", distance=0.85),
    ]
    reference = models.ReferenceModel(document_id="doc-123", workspace_id="ws-456", distance=0.875, chunks=chunks)

    metadata = reference.metadata

    # Standard fields
    assert metadata["documentID"] == "doc-123"
    assert metadata["workspaceID"] == "ws-456"

    # Backwards compatibility: distance at metadata level
    assert metadata["distance"] == 0.875

    # Backwards compatibility: content aggregation
    assert "content" in metadata
    assert metadata["content"]["chunks"] == ["First chunk", "Second chunk"]
    assert metadata["content"]["source"] == "RAG"


def test_post_embeddings_triggers_summary_generation_memory(client):
    """When VECTOR_STORE=memory, tasks should be executed synchronously."""
    from unittest.mock import patch

    from aviator.api import v1

    payload = {"operation": "add", "content": "hello", "metadata": {"documentID": "doc-1"}}

    with (
        patch.object(v1.settings, "vector_store", "memory"),
        patch("aviator.api.v1.process_embedding_request") as mock_process,
        patch("aviator.api.v1.process_workspace_summary_request") as mock_summary,
    ):
        response = client.post("/v1/embeddings", json=payload)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    mock_process.assert_called_once()
    mock_summary.assert_called_once()
    assert not mock_process.delay.called
    assert not mock_summary.delay.called


def test_post_embeddings_triggers_summary_generation_async(client):
    """When VECTOR_STORE!=memory, tasks should be enqueued via .delay()."""
    from unittest.mock import patch

    from aviator.api import v1

    payload = {"operation": "add", "content": "hello", "metadata": {"documentID": "doc-1"}}

    with (
        patch.object(v1.settings, "vector_store", "pgvectorstore"),
        patch("aviator.api.v1.process_embedding_request") as mock_process,
        patch("aviator.api.v1.process_workspace_summary_request") as mock_summary,
    ):
        response = client.post("/v1/embeddings", json=payload)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert not mock_process.called
    assert not mock_summary.called
    mock_process.delay.assert_called_once()
    mock_summary.delay.assert_called_once()


def test_post_metadata_triggers_summary_generation_memory(client):
    """/v1/metadata should pass is_metadata=True and still trigger summary generation."""
    from unittest.mock import patch

    from aviator.api import v1

    payload = {"operation": "add", "content": "metadata", "metadata": {"documentID": "doc-1"}}

    with (
        patch.object(v1.settings, "vector_store", "memory"),
        patch("aviator.api.v1.process_embedding_request") as mock_process,
        patch("aviator.api.v1.process_workspace_summary_request") as mock_summary,
    ):
        response = client.post("/v1/metadata", json=payload)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    mock_process.assert_called_once()
    _, kwargs = mock_process.call_args
    assert kwargs.get("is_metadata") is True
    mock_summary.assert_called_once()


def test_post_metadata_triggers_summary_generation_async(client):
    """/v1/metadata should enqueue both tasks when VECTOR_STORE!=memory."""
    from unittest.mock import patch

    from aviator.api import v1

    payload = {"operation": "add", "content": "metadata", "metadata": {"documentID": "doc-1"}}

    with (
        patch.object(v1.settings, "vector_store", "pgvectorstore"),
        patch("aviator.api.v1.process_embedding_request") as mock_process,
        patch("aviator.api.v1.process_workspace_summary_request") as mock_summary,
    ):
        response = client.post("/v1/metadata", json=payload)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert not mock_process.called
    mock_process.delay.assert_called_once()
    _, kwargs = mock_process.delay.call_args
    assert kwargs.get("is_metadata") is True
    mock_summary.delay.assert_called_once()
