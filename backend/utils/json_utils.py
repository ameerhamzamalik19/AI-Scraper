# utils/json_utils.py
import json
import re
import logging

logger = logging.getLogger(__name__)


def _strip_fences(text: str) -> str:
    """
    Strip a markdown code fence from a string.

    Handles three cases:
      - fully fenced:   ```json\\n{...}\\n```
      - open-only:      ```json\\n{...}       (truncated output)
      - no fence:       {...}

    The open-only case matters because LLM responses are sometimes
    truncated before the closing fence, and we still want to attempt
    parsing the partial content.
    """
    if not text:
        return text
    stripped = text.strip()

    # Case 1: fully fenced. Try to match opening + closing.
    m = re.match(
        r'^```(?:json|JSON)?\s*(.*?)\s*```\s*$',
        stripped,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    # Case 2: opening fence, no closing. Strip the fence line.
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline != -1:
            inner = stripped[first_newline + 1:]
            # If a trailing fence snuck in anyway, drop it.
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3]
            return inner.strip()

    # Case 3: no fence.
    return stripped


def _first_json_value(text: str):
    """
    Scan for the first balanced {...} or [...] and return it as a string.
    Handles nested braces and ignores braces inside string literals.

    Returns None if no matching close is found (e.g. truncated JSON).
    """
    if not text:
        return None
    text = text.strip()
    start = -1
    opener = None
    for i, ch in enumerate(text):
        if ch in '{[':
            start = i
            opener = ch
            break
    if start == -1:
        return None

    closer = '}' if opener == '{' else ']'
    depth = 0
    in_str = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _remove_trailing_commas(text: str) -> str:
    return re.sub(r',(\s*[}\]])', r'\1', text)


def _repair_truncated_json(text: str):
    """
    Best-effort repair of a truncated JSON object or array.

    Strategy: walk the string tracking string/escape state and brace/bracket
    depth. When the string ends mid-value, close the string, then close any
    open braces/brackets. Returns None if the result is still not parseable.

    This is not a general-purpose JSON repair library; it handles the common
    case of an LLM response that hit a token limit mid-structure.
    """
    if not text:
        return None

    # Find the first { or [.
    start = -1
    opener = None
    for i, ch in enumerate(text):
        if ch in '{[':
            start = i
            opener = ch
            break
    if start == -1:
        return None

    text = text[start:]
    closer_stack = []
    in_str = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == '{':
                closer_stack.append('}')
            elif ch == '[':
                closer_stack.append(']')
            elif ch in '}]':
                if closer_stack:
                    closer_stack.pop()
        i += 1

    repaired = text
    # If we ended mid-string, close the string.
    if in_str:
        repaired += '"'
    # If the last non-whitespace char is a comma or colon, remove it.
    repaired = repaired.rstrip()
    while repaired and repaired[-1] in ',:':
        repaired = repaired[:-1].rstrip()
    # Close any open containers.
    while closer_stack:
        repaired += closer_stack.pop()

    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        return None


def safe_json_loads(text: str, *, context: str = ""):
    """
    Parse JSON that may be wrapped in prose, fences, or have minor syntax
    issues. Attempts, in order:

      1. Exact parse of the extracted balanced JSON value.
      2. Parse after removing trailing commas.
      3. Parse after repairing truncated JSON (close open strings and
         containers).

    Returns None on failure and logs a short window around the failure so
    the cause is diagnosable.
    """
    if not text:
        return None

    stripped = _strip_fences(text)
    candidate = _first_json_value(stripped)

    # Build the list of candidates to try.
    attempts = []
    if candidate is not None:
        attempts.append(candidate)
        attempts.append(_remove_trailing_commas(candidate))

    # Truncation repair runs on the stripped text (which may not have a
    # balanced close; that's the whole point of the repair).
    repaired = _repair_truncated_json(stripped)
    if repaired and repaired not in attempts:
        attempts.append(repaired)

    # Fall back to parsing the stripped text directly, in case it's a
    # bare scalar or something _first_json_value didn't recognize.
    if stripped and stripped not in attempts:
        attempts.append(stripped)

    last_err = None
    for attempt in attempts:
        try:
            return json.loads(attempt)
        except json.JSONDecodeError as e:
            last_err = e
            continue

    if last_err is not None:
        # Prefer to report the position from the first (cleanest) attempt
        # so the message is stable across retries.
        try:
            json.loads(attempts[0])
        except json.JSONDecodeError as e0:
            last_err = e0
            report_from = attempts[0]
        else:
            report_from = attempts[0]
        pos = last_err.pos if last_err.pos is not None else 0
        window = report_from[max(0, pos - 120): pos + 120]
        logger.error(
            "Structured data JSON parse failed%s: %s | near: ...%r...",
            f" ({context})" if context else "",
            last_err,
            window,
        )
    return None