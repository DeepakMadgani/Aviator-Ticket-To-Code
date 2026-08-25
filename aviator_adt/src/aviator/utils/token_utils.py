"""Token estimation and batching utilities."""

from collections.abc import Callable


def estimate_tokens(text: str) -> int:
    """Estimate token count from text length.

    Uses a conservative heuristic of ~4 chars per token.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def batch_by_token_limit(
    items: list[str],
    token_limit: int,
    token_fn: Callable[[str], int] | None = None,
) -> list[list[str]]:
    """Batch strings into groups respecting a token limit.

    Args:
        items: List of strings to batch.
        token_limit: Maximum estimated tokens per batch.
        token_fn: Callable to estimate tokens for a string. Defaults to estimate_tokens.

    Returns:
        List of batches, where each batch is a list of strings.

    """
    if token_fn is None:
        token_fn = estimate_tokens

    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_tokens = 0

    for item in items:
        item_tokens = token_fn(item)

        if current_batch and current_tokens + item_tokens > token_limit:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0

        current_batch.append(item)
        current_tokens += item_tokens

    if current_batch:
        batches.append(current_batch)

    return batches
