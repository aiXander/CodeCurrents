"""LLM calls — supports Anthropic, OpenAI, and Gemini backends via model prefix."""

import os
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed


def _detect_backend(model: str) -> str:
    """Detect which API to use based on model name."""
    if model.startswith("claude"):
        return "anthropic"
    elif model.startswith("gpt") or model.startswith("o1") or model.startswith("o3") or model.startswith("o4"):
        return "openai"
    elif model.startswith("gemini"):
        return "gemini"
    else:
        # Default fallback: try anthropic if key exists, then openai, then gemini
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        return "gemini"


def _call_anthropic(system_prompt: str, content: str, model: str) -> str:
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": content}],
        temperature=0.3,
    )
    return response.content[0].text


def _call_openai(system_prompt: str, content: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        temperature=0.3,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
    )
    return response.choices[0].message.content or ""


def _call_gemini(system_prompt: str, content: str, model: str) -> str:
    from google import genai
    from google.genai import types
    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=content,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.3,
            max_output_tokens=2048,
        ),
    )
    return response.text or ""


_BACKENDS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
}


def _call_llm(system_prompt: str, content: str, model: str, max_retries: int = 3) -> str:
    """Call the appropriate LLM backend with retry logic."""
    backend = _detect_backend(model)
    call_fn = _BACKENDS[backend]

    for attempt in range(max_retries):
        try:
            return call_fn(system_prompt, content, model)
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  LLM error ({backend}, attempt {attempt + 1}): {e}. Retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                raise RuntimeError(f"LLM call failed after {max_retries} attempts ({backend}): {e}") from e
    return ""


def map_pass(
    chunks: list[str],
    prompt: str,
    model: str,
    max_workers: int = 3,
) -> list[str]:
    """Run the map pass: summarize each chunk in parallel."""
    if not chunks:
        return []

    summaries: list[str | None] = [None] * len(chunks)

    def process(idx: int, chunk_text: str) -> tuple[int, str]:
        print(f"  Processing chunk {idx + 1}/{len(chunks)} ...", file=sys.stderr)
        result = _call_llm(prompt, chunk_text, model)
        return idx, result

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(process, i, c): i for i, c in enumerate(chunks)}
        for future in as_completed(futures):
            idx, result = future.result()
            summaries[idx] = result

    return [s for s in summaries if s is not None]


def reduce_pass(summaries: list[str], prompt: str, model: str) -> str:
    """Run the reduce pass: aggregate all chunk summaries into one."""
    if not summaries:
        return "NO_MAJOR_CHANGES"

    combined = "\n\n---\n\n".join(
        f"## Batch {i + 1} Summary\n{s}" for i, s in enumerate(summaries)
    )

    print(f"  Running reduce pass on {len(summaries)} summaries ...", file=sys.stderr)
    return _call_llm(prompt, combined, model)
