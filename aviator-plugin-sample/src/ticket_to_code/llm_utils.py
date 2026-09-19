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

            # Fallback for standard workflow (LangGraph threads where context var is lost)
            if run_ctx is None:
                try:
                    from ticket_to_code.workflow import _transient_store, _transient_lock, get_active_ticket_id
                    active_tid = get_active_ticket_id()
                    with _transient_lock:
                        if active_tid and active_tid in _transient_store and "run_ctx" in _transient_store[active_tid]:
                            run_ctx = _transient_store[active_tid]["run_ctx"]
                        else:
                            # Fallback: take the most recent ticket from transient store
                            for tid, store in reversed(list(_transient_store.items())):
                                if "run_ctx" in store:
                                    run_ctx = store["run_ctx"]
                                    break
                except Exception:
                    pass

            if run_ctx and run_ctx.budget:
                usage = extract_token_usage(resp)

                # Estimate input tokens from messages when provider doesn't report them
                input_tokens  = usage.get("prompt_tokens", 0)
                input_source  = usage.get("input_source", "none")
                if input_tokens == 0:
                    try:
                        total_chars = sum(
                            len(getattr(m, "content", "") or "")
                            for m in messages
                        )
                        if total_chars > 0:
                            input_tokens = max(1, total_chars // 4)
                            input_source = "estimated"
                    except Exception:
                        pass  # Estimation is best-effort

                run_ctx.budget.charge(
                    tokens_in=input_tokens,
                    tokens_out=usage.get("completion_tokens", 0),
                    cost_usd=0.0,
                    model=usage.get("model", "unknown"),
                    input_source=input_source,
                    output_source=usage.get("output_source", "none"),
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

    Uses a three-level fallback hierarchy:
      1. ``response_metadata.token_usage`` (provider-reported actual)
      2. ``usage_metadata`` (newer LangChain field, also provider-reported)
      3. Character-based estimation (``len(content) // 4``, marked as estimated)

    Returns:
        dict with keys: model, prompt_tokens, completion_tokens, total_tokens,
                        input_source, output_source
    """
    usage = {
        "model": "unknown",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "input_source": "none",
        "output_source": "none",
    }
    
    try:
        # ── Level 1: response_metadata.token_usage (standard LangChain) ────
        if hasattr(response, "response_metadata"):
            meta = response.response_metadata
            usage["model"] = (
                meta.get("model_name")
                or meta.get("model", "unknown")
            )
            
            token_usage = meta.get("token_usage", {})
            if token_usage:
                pt = token_usage.get("prompt_tokens", 0)
                ct = token_usage.get("completion_tokens", 0)
                tt = token_usage.get("total_tokens", 0)
                if pt or ct or tt:
                    usage["prompt_tokens"] = pt
                    usage["completion_tokens"] = ct
                    usage["total_tokens"] = tt or (pt + ct)
                    if pt:
                        usage["input_source"] = "provider"
                    if ct:
                        usage["output_source"] = "provider"

        # ── Level 2: usage_metadata (newer LangChain / Vertex AI) ──────────
        if usage["prompt_tokens"] == 0 and usage["completion_tokens"] == 0:
            if hasattr(response, "usage_metadata"):
                um = response.usage_metadata
                if isinstance(um, dict):
                    it = um.get("input_tokens", 0)
                    ot = um.get("output_tokens", 0)
                    tt = um.get("total_tokens", 0)
                    if it or ot or tt:
                        usage["prompt_tokens"] = it
                        usage["completion_tokens"] = ot
                        usage["total_tokens"] = tt or (it + ot)
                        if it:
                            usage["input_source"] = "usage_metadata"
                        if ot:
                            usage["output_source"] = "usage_metadata"

        # ── Level 3: Estimate output tokens from response content ──────────
        if usage["completion_tokens"] == 0:
            content = getattr(response, "content", "")
            if content:
                usage["completion_tokens"] = max(1, len(content) // 4)
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
                usage["output_source"] = "estimated"

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
