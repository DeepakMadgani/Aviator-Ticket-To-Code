"""Module for loading Prompts."""

import logging
from pathlib import Path

import aiofiles

from aviator.exceptions import PromptNotFoundError
from aviator.settings import LLMProvider

logger = logging.getLogger(__name__)


class PromptsLoader:
    """Loads prompts with model-specific fallback to model provider defaults and then global defaults."""

    def __init__(self, model_provider: LLMProvider, model: str, prompts_base_dir: Path | None = None) -> None:
        """Initialize PromptsLoader.

        Args:
            model_provider: Model provider (LLMProvider enum).
            model: Model name for locating specific prompts.
            prompts_base_dir: Base directory for prompt files. If None, auto-constructs from module location.

        """
        # Convert enum to string value for path construction
        self.model_provider = model_provider.value
        self.model = model
        self.prompts_base_dir = (
            Path(prompts_base_dir) if prompts_base_dir else (Path(__file__).parent.parent / "prompts")
        )
        # Cache for loaded prompts
        self._prompt_cache: dict[str, str] = {}

    async def _try_load_file(self, file_path: Path) -> str | None:
        """Attempt to load a file from the given path.

        Args:
            file_path: Path to the file to load.

        Returns:
            File content if found, None otherwise.

        """
        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                return await f.read()
        except FileNotFoundError:
            return None

    async def load_prompt(self, prompt_name: str, model: str | None = None) -> str:
        """Load prompt file with hierarchical fallback.

        Tries in order:
        1. {model_provider}/{model}/{prompt_name}.md
        2. {model_provider}/{prompt_name}.md (provider default)
        3. {prompt_name}.md (global default)

        Args:
            prompt_name: Prompt file name without .md extension.
            model: Optional model name to use. If None, uses instance's default model.

        Returns:
            Prompt file content.

        Raises:
            PromptNotFoundError: If prompt not found in any location.

        """
        # Use provided model or fall back to instance defaults
        target_model = model or self.model
        filename = f"{prompt_name}.md"

        # Check cache first
        cache_key = f"{prompt_name}:{target_model}"
        if cache_key in self._prompt_cache:
            logger.debug("Returning cached prompt: %s for model: %s", prompt_name, target_model)
            return self._prompt_cache[cache_key]

        # Define fallback paths to try in order
        paths_to_try = [
            # Try 1: model-specific path ({provider}/{model}/{prompt}.md)
            (self.prompts_base_dir / self.model_provider / target_model / filename, "model specific"),
            # Try 2: provider default path ({provider}/{prompt}.md)
            (self.prompts_base_dir / self.model_provider / filename, "provider default"),
            # Try 3: global default path ({prompt}.md)
            (self.prompts_base_dir / filename, "global default"),
        ]

        # Try each path in order
        for path, prompt_type in paths_to_try:
            logger.debug("Attempting to load %s prompt: %s", prompt_type, prompt_name)
            prompt_content = await self._try_load_file(path)
            if prompt_content is not None:
                logger.info("Loaded %s prompt: %s", prompt_type, prompt_name)
                self._prompt_cache[cache_key] = prompt_content
                return prompt_content
            logger.debug("%s prompt not found: %s", prompt_type.capitalize(), path)

        # Not found in any location
        logger.error(
            "Prompt not found: %s (provider: %s, model: %s). Searched paths: %s",
            prompt_name,
            self.model_provider,
            target_model,
            [str(path) for path, _ in paths_to_try],
        )
        raise PromptNotFoundError(prompt_name, target_model)
