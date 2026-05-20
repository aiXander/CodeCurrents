"""LLM calls — supports OpenRouter, Anthropic, OpenAI, and Gemini backends via model prefix."""

import asyncio
import os
import sys
from typing import Awaitable, Callable


def _detect_backend(model: str) -> str:
    # OpenRouter models use provider/model-name format
    if "/" in model:
        return "openrouter"
    if model.startswith("claude"):
        return "anthropic"
    elif model.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    elif model.startswith("gemini"):
        return "gemini"
    else:
        if os.environ.get("OPENROUTER_API_KEY"):
            return "openrouter"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        return "gemini"


async def _call_openrouter(system_prompt: str, content: str, model: str) -> str:
    from openai import AsyncOpenAI
    async with AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
    ) as client:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=2048,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        return response.choices[0].message.content or ""


async def _call_anthropic(system_prompt: str, content: str, model: str) -> str:
    import anthropic
    async with anthropic.AsyncAnthropic() as client:
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            temperature=0.3,
        )
        return response.content[0].text


async def _call_openai(system_prompt: str, content: str, model: str) -> str:
    from openai import AsyncOpenAI
    async with AsyncOpenAI() as client:
        response = await client.chat.completions.create(
            model=model,
            max_tokens=2048,
            temperature=0.3,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        return response.choices[0].message.content or ""


async def _call_gemini(system_prompt: str, content: str, model: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client()
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=content,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=2048,
            ),
        )
        return response.text or ""
    finally:
        aclose = getattr(client, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                pass


_BACKENDS = {
    "openrouter": _call_openrouter,
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


async def _call_llm(system_prompt: str, content: str, model: str, max_retries: int = 3) -> str:
    backend = _detect_backend(model)
    call_fn = _BACKENDS[backend]

    for attempt in range(max_retries):
        try:
            return await call_fn(system_prompt, content, model)
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  LLM error ({backend}, attempt {attempt + 1}): {e}. Retrying in {wait}s...", file=sys.stderr)
                await asyncio.sleep(wait)
            else:
                raise RuntimeError(f"LLM call failed after {max_retries} attempts ({backend}): {e}") from e
    return ""


# Map-pass results preserve chunk order. None means "this chunk failed all retries".
MapResult = str | None


async def map_pass(
    chunks: list[str],
    prompt: str,
    model: str,
    max_concurrent: int = 8,
    on_success: Callable[[int, str], Awaitable[None] | None] | None = None,
) -> list[MapResult]:
    """Summarize each chunk concurrently, capped at `max_concurrent` in flight.

    Results are positionally aligned to `chunks` — failures become None in the
    returned list rather than being dropped (so downstream code can flag them).
    If `on_success` is provided, it's called with `(index, summary)` as soon as
    each chunk completes successfully — useful for streaming summaries to disk.
    """
    if not chunks:
        return []

    backend = _detect_backend(model)
    print(f"  Model: {model} (backend: {backend}, max concurrent: {max_concurrent})", file=sys.stderr)

    sem = asyncio.Semaphore(max(1, max_concurrent))
    results: list[MapResult] = [None] * len(chunks)

    async def process(idx: int, chunk_text: str) -> None:
        async with sem:
            print(f"  Processing chunk {idx + 1}/{len(chunks)} ...", file=sys.stderr)
            try:
                summary = await _call_llm(prompt, chunk_text, model)
            except Exception as e:
                print(f"  Chunk {idx + 1} failed permanently: {e}. Marking as failed.", file=sys.stderr)
                return
            results[idx] = summary
            if on_success is not None:
                maybe_awaitable = on_success(idx, summary)
                if asyncio.iscoroutine(maybe_awaitable):
                    await maybe_awaitable

    await asyncio.gather(*[process(i, c) for i, c in enumerate(chunks)])
    return results


async def reduce_pass(summaries: list[str], prompt: str, model: str) -> str:
    """Aggregate all chunk summaries into one final narrative."""
    if not summaries:
        return "NO_MAJOR_CHANGES"

    combined = "\n\n---\n\n".join(
        f"## Batch {i + 1} Summary\n{s}" for i, s in enumerate(summaries)
    )

    backend = _detect_backend(model)
    print(f"  Running reduce pass on {len(summaries)} summaries (model: {model}, backend: {backend}) ...", file=sys.stderr)
    return await _call_llm(prompt, combined, model)
