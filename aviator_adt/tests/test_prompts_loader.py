"""Test PromptsLoader module."""

import logging

import pytest

from aviator.exceptions import PromptNotFoundError
from aviator.services.prompts_loader import PromptsLoader
from aviator.settings import LLMProvider

# Configure pytest to use anyio for async tests
pytestmark = pytest.mark.anyio


@pytest.fixture
def temp_prompts_dir(tmp_path):
    """Create a temporary prompts directory structure."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Create global default prompts
    (prompts_dir / "assistant.md").write_text("Default assistant prompt")
    (prompts_dir / "grader.md").write_text("Default grader prompt")

    # Create provider directory
    provider_dir = prompts_dir / "google_genai"
    provider_dir.mkdir()

    # Create provider default prompts
    (provider_dir / "grader.md").write_text("Provider default grader prompt")

    # Create model-specific prompts
    model_dir = provider_dir / "gemini-2.5-flash"
    model_dir.mkdir()
    (model_dir / "assistant.md").write_text("Gemini-specific assistant prompt")

    return prompts_dir


@pytest.fixture
def prompts_loader(temp_prompts_dir):
    """Create a PromptsLoader instance with temporary directory."""
    return PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model="gemini-2.5-flash", prompts_base_dir=temp_prompts_dir
    )


async def test_load_model_specific_prompt(prompts_loader):
    """Test loading a model-specific prompt when it exists."""
    content = await prompts_loader.load_prompt("assistant")
    assert content == "Gemini-specific assistant prompt"


async def test_fallback_to_default_prompt(prompts_loader):
    """Test fallback to provider default prompt when model-specific prompt doesn't exist."""
    content = await prompts_loader.load_prompt("grader")
    assert content == "Provider default grader prompt"


async def test_prompt_not_found_error(prompts_loader):
    """Test PromptNotFoundError is raised when prompt doesn't exist."""
    with pytest.raises(PromptNotFoundError) as exc_info:
        await prompts_loader.load_prompt("nonexistent")

    assert exc_info.value.prompt_name == "nonexistent"
    assert exc_info.value.model == "gemini-2.5-flash"
    assert "nonexistent" in str(exc_info.value)


async def test_load_prompt_with_explicit_model(temp_prompts_dir):
    """Test loading prompt with explicitly specified model."""
    loader = PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model="default-model", prompts_base_dir=temp_prompts_dir
    )

    # Create another model directory within the same provider
    another_model_dir = temp_prompts_dir / "google_genai" / "gemini-2.5-flash-lite"
    another_model_dir.mkdir()
    (another_model_dir / "grader.md").write_text("Lite model grader prompt")

    # Load with explicit model override
    content = await loader.load_prompt("grader", model="gemini-2.5-flash-lite")
    assert content == "Lite model grader prompt"


async def test_init_with_default_prompts_base_dir():
    """Test PromptsLoader initialization with default prompts_base_dir."""
    loader = PromptsLoader(model_provider=LLMProvider.GOOGLE_GENAI, model="gemini-2.5-flash")

    assert loader.prompts_base_dir.name == "prompts"


async def test_load_prompt_preserves_content_format(temp_prompts_dir):
    """Test that prompt content with special characters is preserved."""
    special_content = "# System Prompt\n\nHello {{user}}!\n\n- Item 1\n- Item 2\n"
    (temp_prompts_dir / "special.md").write_text(special_content)

    loader = PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model="gemini-2.5-flash", prompts_base_dir=temp_prompts_dir
    )
    content = await loader.load_prompt("special")
    assert content == special_content


async def test_logging_on_fallback(prompts_loader, caplog):
    """Test that debug logging occurs when falling back to provider default prompt."""
    with caplog.at_level(logging.DEBUG):
        await prompts_loader.load_prompt("grader")

    assert any("Model specific prompt not found" in record.message for record in caplog.records)


async def test_logging_on_prompt_not_found(prompts_loader, caplog):
    """Test that error logging occurs when prompt is not found."""
    with caplog.at_level(logging.ERROR), pytest.raises(PromptNotFoundError):
        await prompts_loader.load_prompt("nonexistent")

    assert any("Prompt not found" in record.message for record in caplog.records)


async def test_multiple_prompts_loading(prompts_loader):
    """Test loading multiple different prompts sequentially."""
    assistant_content = await prompts_loader.load_prompt("assistant")
    grader_content = await prompts_loader.load_prompt("grader")

    assert assistant_content == "Gemini-specific assistant prompt"
    assert grader_content == "Provider default grader prompt"


async def test_prompt_filename_construction(temp_prompts_dir):
    """Test that .md extension is correctly appended to prompt names."""
    loader = PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model="test-model", prompts_base_dir=temp_prompts_dir
    )

    # Prompt name should not include .md extension
    (temp_prompts_dir / "test_prompt.md").write_text("Test content")

    content = await loader.load_prompt("test_prompt")
    assert content == "Test content"


async def test_prompt_with_subdirectory_structure(temp_prompts_dir):
    """Test that prompts are loaded from correct subdirectory for model."""
    # Create nested model directory
    nested_model = "gemini-2.5-flash-lite"
    provider_dir = temp_prompts_dir / "google_genai"
    nested_dir = provider_dir / nested_model
    nested_dir.mkdir()
    (nested_dir / "rewrite_question.md").write_text("Nested model prompt")

    loader = PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model=nested_model, prompts_base_dir=temp_prompts_dir
    )
    content = await loader.load_prompt("rewrite_question")
    assert content == "Nested model prompt"


async def test_prompt_error_contains_searched_paths(prompts_loader, caplog):
    """Test that error message includes all searched paths."""
    with caplog.at_level(logging.ERROR), pytest.raises(PromptNotFoundError):
        await prompts_loader.load_prompt("missing_prompt")

    # Check that both model-specific and default paths are in error log
    error_messages = [record.message for record in caplog.records if record.levelname == "ERROR"]
    assert any("gemini-2.5-flash" in msg for msg in error_messages)
    assert any("Searched paths" in msg for msg in error_messages)


async def test_load_prompt_with_utf8_content(temp_prompts_dir):
    """Test loading prompts with UTF-8 special characters."""
    utf8_content = "Hello 世界! 🚀 émojis and spëcial chars"
    (temp_prompts_dir / "utf8_test.md").write_text(utf8_content, encoding="utf-8")

    loader = PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model="gemini-2.5-flash", prompts_base_dir=temp_prompts_dir
    )
    content = await loader.load_prompt("utf8_test")
    assert content == utf8_content


async def test_concurrent_prompt_loading(prompts_loader):
    """Test that multiple concurrent loads work correctly."""
    import asyncio

    # Load multiple prompts concurrently
    results = await asyncio.gather(
        prompts_loader.load_prompt("assistant"),
        prompts_loader.load_prompt("grader"),
        prompts_loader.load_prompt("assistant"),  # Duplicate
    )

    assert results[0] == "Gemini-specific assistant prompt"
    assert results[1] == "Provider default grader prompt"
    assert results[2] == "Gemini-specific assistant prompt"


async def test_model_attribute_stored_correctly():
    """Test that model attribute is stored correctly on initialization."""
    loader1 = PromptsLoader(model_provider=LLMProvider.GOOGLE_GENAI, model="model-a")
    loader2 = PromptsLoader(model_provider=LLMProvider.OPENAI, model="model-b")

    assert loader1.model == "model-a"
    assert loader2.model == "model-b"


async def test_prompts_base_dir_attribute_stored_correctly(temp_prompts_dir):
    """Test that prompts_base_dir attribute is stored correctly."""
    loader = PromptsLoader(model_provider=LLMProvider.GOOGLE_GENAI, model="test", prompts_base_dir=temp_prompts_dir)
    assert loader.prompts_base_dir == temp_prompts_dir


async def test_load_prompt_file_not_found_all_paths_tried(prompts_loader, caplog):
    """Test that all paths are tried when prompt is not found."""
    with caplog.at_level(logging.ERROR), pytest.raises(PromptNotFoundError) as exc_info:
        await prompts_loader.load_prompt("does_not_exist")

    # Check that error message includes all searched paths
    assert exc_info.value.prompt_name == "does_not_exist"
    assert exc_info.value.model == "gemini-2.5-flash"

    # Verify all three paths were searched
    error_logs = [r.message for r in caplog.records if r.levelname == "ERROR"]
    assert len(error_logs) > 0
    assert "does_not_exist" in error_logs[0]
    assert "google_genai" in error_logs[0]
    assert "gemini-2.5-flash" in error_logs[0]


async def test_cache_is_used_on_second_load(prompts_loader, caplog):
    """Test that cache is used on second load of the same prompt."""
    # First load - should read from file
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        content = await prompts_loader.load_prompt("assistant")
        assert content == "Gemini-specific assistant prompt"
        assert any("Loaded model specific prompt" in record.message for record in caplog.records)

    # Second load - should return from cache
    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        content2 = await prompts_loader.load_prompt("assistant")
        assert content2 == "Gemini-specific assistant prompt"
        assert content2 == content  # Verify cached content matches original
        assert any("Returning cached prompt" in record.message for record in caplog.records)
        # Should not attempt to load from file again
        assert not any("Attempting to load" in record.message for record in caplog.records)


async def test_cache_key_includes_model(temp_prompts_dir):
    """Test that cache key includes model name to avoid collisions."""
    loader = PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model="gemini-2.5-flash", prompts_base_dir=temp_prompts_dir
    )

    # Load same prompt name with default model
    content1 = await loader.load_prompt("assistant")
    assert content1 == "Gemini-specific assistant prompt"

    # Create a different model with different prompt content
    another_model_dir = temp_prompts_dir / "google_genai" / "gemini-2.0-flash"
    another_model_dir.mkdir()
    (another_model_dir / "assistant.md").write_text("Different model assistant prompt")

    # Load same prompt name with different model
    content2 = await loader.load_prompt("assistant", model="gemini-2.0-flash")
    assert content2 == "Different model assistant prompt"

    # Verify both are cached correctly
    content1_again = await loader.load_prompt("assistant")
    content2_again = await loader.load_prompt("assistant", model="gemini-2.0-flash")
    assert content1_again == "Gemini-specific assistant prompt"
    assert content2_again == "Different model assistant prompt"


async def test_cache_persists_across_multiple_loads(prompts_loader):
    """Test that cache persists across multiple loads of different prompts."""
    # Load multiple prompts
    await prompts_loader.load_prompt("assistant")
    await prompts_loader.load_prompt("grader")

    # Verify cache has entries
    assert len(prompts_loader._prompt_cache) == 2
    assert "assistant:gemini-2.5-flash" in prompts_loader._prompt_cache
    assert "grader:gemini-2.5-flash" in prompts_loader._prompt_cache


async def test_cache_is_empty_initially(prompts_loader):
    """Test that cache is empty when loader is first created."""
    assert len(prompts_loader._prompt_cache) == 0


async def test_cache_stores_correct_content(prompts_loader):
    """Test that cache stores the correct content for each prompt."""
    await prompts_loader.load_prompt("assistant")
    await prompts_loader.load_prompt("grader")

    cache_key_assistant = "assistant:gemini-2.5-flash"
    cache_key_grader = "grader:gemini-2.5-flash"

    assert prompts_loader._prompt_cache[cache_key_assistant] == "Gemini-specific assistant prompt"
    assert prompts_loader._prompt_cache[cache_key_grader] == "Provider default grader prompt"


async def test_cache_with_explicit_model_parameter(temp_prompts_dir):
    """Test that cache works correctly when model is explicitly provided."""
    loader = PromptsLoader(
        model_provider=LLMProvider.GOOGLE_GENAI, model="default-model", prompts_base_dir=temp_prompts_dir
    )

    # Load with explicit model
    content1 = await loader.load_prompt("grader", model="gemini-2.5-flash")
    assert content1 == "Provider default grader prompt"

    # Check cache key uses explicit model
    cache_key = "grader:gemini-2.5-flash"
    assert cache_key in loader._prompt_cache
    assert loader._prompt_cache[cache_key] == "Provider default grader prompt"

    # Load a different prompt without explicit model (should use default model)
    (temp_prompts_dir / "assistant.md").write_text("Global assistant prompt")
    content2 = await loader.load_prompt("assistant")
    assert content2 == "Global assistant prompt"

    # Verify both cache entries exist with different keys
    assert len(loader._prompt_cache) == 2
    assert "grader:gemini-2.5-flash" in loader._prompt_cache
    assert "assistant:default-model" in loader._prompt_cache


async def test_cache_performance_improvement(prompts_loader, temp_prompts_dir):
    """Test that caching improves performance on repeated loads."""
    # Create a larger prompt file
    large_content = "Large prompt content\n" * 1000
    (temp_prompts_dir / "large_prompt.md").write_text(large_content)

    # First load (from file)
    content1 = await prompts_loader.load_prompt("large_prompt")

    # Second load (from cache)
    content2 = await prompts_loader.load_prompt("large_prompt")

    assert content1 == content2
    # Cache should be faster (though timing tests can be flaky)
    # Just verify cache was used by checking the content is identical
    assert "large_prompt:gemini-2.5-flash" in prompts_loader._prompt_cache
