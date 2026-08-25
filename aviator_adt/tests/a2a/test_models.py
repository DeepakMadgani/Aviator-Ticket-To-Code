"""Tests for A2A Pydantic models."""

import json

import pytest
from pydantic import ValidationError

from aviator.a2a.models import (
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_TASK_NOT_CANCELABLE,
    JSONRPC_TASK_NOT_FOUND,
    A2AAgentInvokeRequest,
    A2AAgentInvokeResponse,
    Artifact,
    DataPart,
    FilePart,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    Skill,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)

# ── JSON-RPC constants ────────────────────────────────────────────────────────


class TestJsonRpcConstants:
    """Tests for JSON-RPC 2.0 error code constants."""

    def test_standard_error_codes(self):
        assert JSONRPC_INVALID_REQUEST == -32600
        assert JSONRPC_METHOD_NOT_FOUND == -32601
        assert JSONRPC_INVALID_PARAMS == -32602
        assert JSONRPC_INTERNAL_ERROR == -32603

    def test_a2a_specific_codes(self):
        assert JSONRPC_TASK_NOT_FOUND == -32001
        assert JSONRPC_TASK_NOT_CANCELABLE == -32002


# ── Skill model ───────────────────────────────────────────────────────────────


class TestSkill:
    """Tests for the Skill model."""

    def test_minimal_skill(self):
        skill = Skill(id="rag", name="RAG", description="Retrieval augmented generation")
        assert skill.id == "rag"
        assert skill.name == "RAG"
        assert skill.inputModes == ["text/plain"]
        assert skill.outputModes == ["text/plain"]
        assert skill.examples == []
        assert skill.tags == []

    def test_full_skill(self):
        skill = Skill(
            id="chart",
            name="Chart Generator",
            description="Generates charts",
            inputModes=["text/plain", "application/json"],
            outputModes=["image/png"],
            examples=["Draw a bar chart"],
            tags=["visualization"],
        )
        assert skill.inputModes == ["text/plain", "application/json"]
        assert skill.tags == ["visualization"]
        assert skill.examples == ["Draw a bar chart"]


# ── A2AAgentInvokeRequest ─────────────────────────────────────────────────────


class TestA2AAgentInvokeRequest:
    """Tests for A2AAgentInvokeRequest normalisation logic."""

    def _base_message(self, text="Hello", role="user", context_id="thread-xyz"):
        return {
            "message": {
                "role": role,
                "parts": [{"text": text}],
                "contextId": context_id,
                "metadata": {},
            }
        }

    def test_basic_normalization(self):
        req = A2AAgentInvokeRequest.model_validate(self._base_message("What is X?"))
        assert len(req.messages) == 1
        assert req.messages[0].content == "What is X?"
        assert req.messages[0].author == "user"

    def test_context_id_preserved(self):
        req = A2AAgentInvokeRequest.model_validate(self._base_message(context_id="my-thread"))
        context = json.loads(req.context)
        assert context["thread_id"] == "my-thread"

    def test_context_id_auto_generated_when_missing(self):
        payload = {
            "message": {
                "role": "user",
                "parts": [{"text": "test"}],
            }
        }
        req = A2AAgentInvokeRequest.model_validate(payload)
        context = json.loads(req.context)
        assert "thread_id" in context
        assert len(context["thread_id"]) > 0

    def test_agent_role_maps_to_assistant(self):
        req = A2AAgentInvokeRequest.model_validate(self._base_message(role="agent"))
        assert req.messages[0].author == "assistant"

    def test_unknown_role_defaults_to_user(self):
        req = A2AAgentInvokeRequest.model_validate(self._base_message(role="system"))
        assert req.messages[0].author == "user"

    def test_multiple_parts_joined(self):
        payload = {
            "message": {
                "role": "user",
                "parts": [{"text": "Hello"}, {"text": "World"}],
                "contextId": "ctx-1",
            }
        }
        req = A2AAgentInvokeRequest.model_validate(payload)
        assert req.messages[0].content == "Hello World"

    def test_where_from_metadata(self):
        payload = {
            "message": {
                "role": "user",
                "parts": [{"text": "search"}],
                "metadata": {"where": [{"workspaceID": "ws-1"}]},
            }
        }
        req = A2AAgentInvokeRequest.model_validate(payload)
        assert len(req.where) == 1

    def test_inlinecitation_from_metadata(self):
        payload = {
            "message": {
                "role": "user",
                "parts": [{"text": "test"}],
            },
            "inlineCitation": False,
        }
        req = A2AAgentInvokeRequest.model_validate(payload)
        assert req.inlineCitation is False

    def test_inlinecitation_defaults_to_true(self):
        req = A2AAgentInvokeRequest.model_validate(self._base_message())
        assert req.inlineCitation is True

    def test_reference_task_ids_in_context(self):
        payload = {
            "message": {
                "role": "user",
                "parts": [{"text": "follow up"}],
                "referenceTaskIds": ["task-1", "task-2"],
            }
        }
        req = A2AAgentInvokeRequest.model_validate(payload)
        context = json.loads(req.context)
        assert context["reference_task_ids"] == ["task-1", "task-2"]

    def test_missing_message_raises(self):
        with pytest.raises((TypeError, ValidationError)):
            A2AAgentInvokeRequest.model_validate({"context": "{}"})

    def test_non_dict_message_raises(self):
        with pytest.raises((TypeError, ValidationError)):
            A2AAgentInvokeRequest.model_validate({"message": "not-a-dict"})

    def test_empty_parts_raises_validation_error(self):
        payload = {
            "message": {
                "role": "user",
                "parts": [],
            }
        }
        with pytest.raises(ValidationError, match="parts"):
            A2AAgentInvokeRequest.model_validate(payload)


# ── A2AAgentInvokeResponse ────────────────────────────────────────────────────


class TestA2AAgentInvokeResponse:
    """Tests for A2AAgentInvokeResponse defaults."""

    def test_minimal_response(self):
        resp = A2AAgentInvokeResponse()
        assert resp.result is None
        assert resp.context is None
        assert resp.where == []
        assert resp.references == []

    def test_full_response(self):
        resp = A2AAgentInvokeResponse(
            result="Here is the answer.",
            context='{"thread_id": "abc"}',
            where=[],
            references=[{"documentID": "doc-1"}],
        )
        assert resp.result == "Here is the answer."
        assert resp.references == [{"documentID": "doc-1"}]


# ── TaskState ─────────────────────────────────────────────────────────────────


class TestTaskState:
    """Tests for the TaskState enum values."""

    def test_all_states_exist(self):
        assert TaskState.SUBMITTED == "submitted"
        assert TaskState.WORKING == "working"
        assert TaskState.INPUT_REQUIRED == "input-required"
        assert TaskState.COMPLETED == "completed"
        assert TaskState.CANCELED == "canceled"
        assert TaskState.FAILED == "failed"


# ── Part types ────────────────────────────────────────────────────────────────


class TestTextPart:
    """Tests for the TextPart model."""

    def test_basic(self):
        part = TextPart(text="hello")
        assert part.text == "hello"
        assert part.metadata is None

    def test_with_metadata(self):
        part = TextPart(text="hello", metadata={"key": "val"})
        assert part.metadata == {"key": "val"}


class TestDataPart:
    """Tests for the DataPart model."""

    def test_basic(self):
        part = DataPart(data={"key": "value"})
        assert part.data == {"key": "value"}
        assert part.mediaType == "application/json"


class TestFilePart:
    """Tests for the FilePart model."""

    def test_basic(self):
        part = FilePart(filename="doc.pdf", mediaType="application/pdf", url="https://example.com/doc.pdf")
        assert part.filename == "doc.pdf"
        assert part.url == "https://example.com/doc.pdf"
        assert part.raw is None


# ── Artifact ──────────────────────────────────────────────────────────────────


class TestArtifact:
    """Tests for the Artifact model."""

    def test_artifact_with_text_part(self):
        artifact = Artifact(name="response", parts=[TextPart(text="Answer here")])
        assert artifact.name == "response"
        assert len(artifact.parts) == 1
        assert artifact.artifactId  # auto-generated UUID

    def test_artifact_id_auto_generated(self):
        a1 = Artifact(parts=[TextPart(text="a")])
        a2 = Artifact(parts=[TextPart(text="b")])
        assert a1.artifactId != a2.artifactId


# ── Task ──────────────────────────────────────────────────────────────────────


class TestTask:
    """Tests for the Task model."""

    def test_minimal_task(self):
        task = Task(
            id="task-1",
            status=TaskStatus(state=TaskState.SUBMITTED),
        )
        assert task.id == "task-1"
        assert task.kind == "task"
        assert task.contextId is None
        assert task.artifacts == []

    def test_task_with_artifacts(self):
        task = Task(
            id="task-2",
            contextId="ctx-1",
            status=TaskStatus(state=TaskState.COMPLETED),
            artifacts=[Artifact(name="result", parts=[TextPart(text="Done")])],
        )
        assert task.contextId == "ctx-1"
        assert len(task.artifacts) == 1


# ── JSON-RPC envelope models ──────────────────────────────────────────────────


class TestJsonRpcRequest:
    """Tests for the JsonRpcRequest envelope model."""

    def test_valid_request(self):
        req = JsonRpcRequest(method="message/send", params={"key": "val"})
        assert req.jsonrpc == "2.0"
        assert req.method == "message/send"
        assert req.params == {"key": "val"}

    def test_invalid_jsonrpc_version(self):
        with pytest.raises(ValidationError):
            JsonRpcRequest(jsonrpc="1.0", method="message/send")

    def test_missing_method_raises(self):
        with pytest.raises(ValidationError):
            JsonRpcRequest.model_validate({"jsonrpc": "2.0", "id": "1"})


class TestJsonRpcError:
    """Tests for the JsonRpcError model."""

    def test_basic_error(self):
        err = JsonRpcError(code=-32600, message="Invalid Request")
        assert err.code == -32600
        assert err.message == "Invalid Request"
        assert err.data is None


class TestJsonRpcResponse:
    """Tests for the JsonRpcResponse envelope model."""

    def test_success_response(self):
        resp = JsonRpcResponse(id="req-1", result={"answer": "Yes"})
        assert resp.jsonrpc == "2.0"
        assert resp.result == {"answer": "Yes"}
        assert resp.error is None

    def test_error_response(self):
        resp = JsonRpcResponse(id="req-1", error=JsonRpcError(code=-32600, message="Bad request"))
        assert resp.error.code == -32600
        assert resp.result is None


# ── SSE events ────────────────────────────────────────────────────────────────


class TestTaskStatusUpdateEvent:
    """Tests for TaskStatusUpdateEvent."""

    def test_basic(self):
        event = TaskStatusUpdateEvent(statusUpdate={"taskId": "t1", "status": {"state": "submitted"}, "final": False})
        assert event.statusUpdate.taskId == "t1"


class TestTaskArtifactUpdateEvent:
    """Tests for TaskArtifactUpdateEvent."""

    def test_basic(self):
        event = TaskArtifactUpdateEvent(artifactUpdate={"taskId": "t1", "artifact": {"parts": [{"text": "hello"}]}})
        assert event.artifactUpdate.taskId == "t1"
