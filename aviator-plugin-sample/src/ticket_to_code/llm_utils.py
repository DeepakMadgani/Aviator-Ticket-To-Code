"""
LLM utility helpers shared across all agents.
"""
import logging
import time

logger = logging.getLogger(__name__)

# Network / transient error types to retry on
_RETRYABLE_ERRORS = (
    "ReadError", "ConnectError", "RemoteProtocolError",
    "WinError 10054", "WinError 10053", "WinError 10061",
    "Connection reset", "forcibly closed", "Connection refused",
    "503", "429", "ResourceExhausted",
    # OAuth2 / Vertex AI token-refresh timeouts (ConnectTimeoutError wraps urllib3)
    "ConnectTimeout", "TimeoutError", "timed out",
    "Max retries exceeded",
)


def llm_invoke(llm, messages, max_retries: int = 5, base_delay: float = 5.0):
    """
    Invoke an LLM with automatic retry on transient network/API errors.

    Retries up to `max_retries` times with exponential backoff.
    Raises the original exception if all retries fail.
    """
    llm_type = type(llm)
    llm_module = getattr(llm_type, "__module__", "")
    if llm_module.startswith("unittest.mock"):
        raise RuntimeError(
            "Invalid mocked LLM instance detected in runtime path "
            f"({llm_type.__name__}). Check LLMRegistry wiring and remove test mocks."
        )

    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = llm.invoke(messages)
            
            # B11: Charge budget if run context is active
            from ticket_to_code.runtime.run_context import current_run_context
            run_ctx = current_run_context.get(None)
            if run_ctx and run_ctx.budget:
                usage = extract_token_usage(resp)
                run_ctx.budget.charge(
                    tokens_in=usage.get("prompt_tokens", 0),
                    tokens_out=usage.get("completion_tokens", 0),
                    cost_usd=0.0
                )
            
            return resp
        except Exception as exc:
            exc_str = f"{type(exc).__name__}: {str(exc)} {repr(exc)}"
            is_retryable = any(e.lower() in exc_str.lower() for e in _RETRYABLE_ERRORS)
            # Also catch by exception class name
            is_retryable = is_retryable or any(
                e.lower() in type(exc).__name__.lower() for e in ("ReadError", "ConnectError", "RemoteProtocolError", "Timeout", "Connection")
            )
            if is_retryable and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"LLM network error (attempt {attempt + 1}/{max_retries}): {exc}. "
                    f"Retrying in {delay:.0f}s..."
                )
                time.sleep(delay)
                last_exc = exc
                continue
            raise  # non-retryable or last attempt
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("LLM invocation failed (max_retries=0 or unknown error)")


def extract_token_usage(response) -> dict:
    """
    Extract token usage metrics from a LangChain AIMessage response.
    Returns:
        dict with keys: model, prompt_tokens, completion_tokens, total_tokens
    """
    usage = {
        "model": "unknown",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0
    }
    
    try:
        # Check standard LangChain AIMessage response_metadata
        if hasattr(response, "response_metadata"):
            meta = response.response_metadata
            usage["model"] = meta.get("model_name", "unknown")
            
            token_usage = meta.get("token_usage", {})
            if token_usage:
                usage["prompt_tokens"] = token_usage.get("prompt_tokens", 0)
                usage["completion_tokens"] = token_usage.get("completion_tokens", 0)
                usage["total_tokens"] = token_usage.get("total_tokens", 0)
    except Exception as e:
        logger.debug(f"Failed to extract token usage: {e}")
        
    return usage


def expand_ticket_keywords(ticket_text: str) -> dict:
    """
    Expand key concepts (versions, config keys, identifiers) from ticket text into
    common codebase permutations. The LLM determines the appropriate extraction level.
    Returns a dict with 'current_state_literals' and 'desired_state_literals'.
    """
    import json
    import re
    from aviator.services.llm import LLMRegistry
    from langchain_core.messages import SystemMessage, HumanMessage
    
    llm = LLMRegistry.get_llm(assistant=False)
    
    prompt = f"""
You are an expert software engineer extracting literals from a ticket description.
Your task is to identify key terms (e.g. versions, configuration keys, specific names) and expand them into common codebase permutations.

Ticket Text:
{ticket_text}

First, determine the appropriate level of extraction based on the ticket context:
- low: For general bugs or UI changes. Only extract highly obvious, explicit values. Minimal permutations.
- medium: For general API changes or features. Moderate permutations (e.g., camelCase, snake_case).
- high: For version bumps or configuration updates. Aggressively extract all identifiers and extensively permute them (e.g., dots to underscores, flat numbers like 260300).

Then, perform the extraction and return ONLY a JSON object in this format:
{{
  "current_state_literals": ["list of strings that might represent the current state in the code"],
  "desired_state_literals": ["list of strings that might represent the desired/new state in the code"]
}}
"""
    try:
        resp = llm_invoke(llm, [SystemMessage(content="You are a helpful assistant."), HumanMessage(content=prompt)])
        content = getattr(resp, "content", "")
        # extract JSON
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
        if fenced:
            raw = fenced.group(1)
        else:
            brace = re.search(r"\{.*\}", content, re.DOTALL)
            raw = brace.group(0) if brace else "{}"
        
        data = json.loads(raw)
        return {
            "current_state_literals": data.get("current_state_literals", []),
            "desired_state_literals": data.get("desired_state_literals", [])
        }
    except Exception as exc:
        logger.warning(f"Failed to expand ticket keywords: {exc}")
        return {"current_state_literals": [], "desired_state_literals": []}
