"""Private request transport for the Linux/CUDA Qwen3.6 llguidance decoder.

No model downloads; vocabulary bytes come from the tokenizer already used by Service.
The engine checks every byte against its GGUF metadata before generating.
"""
import contextlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
from serve.structured import StructuredOutputError


def supported(engine):
    return bool(getattr(engine, "json_schema_llguidance", False)) and not getattr(engine, "batch", 0) and (not hasattr(engine, "alive") or engine.alive())


REASONS = {
    "not_compiled": "The running strata-poly executable was built without STRATA_POLY_LLGUIDANCE=ON.",
    "drafts_enabled": "The running engine has draft generation enabled; add --no-draft and restart it.",
    "batching_enabled": "Batching is enabled; remove --batch and restart the engine.",
    "model_name_mismatch": "GGUF general.name did not identify Qwen3.6; the metadata name check rejected it.",
    "architecture_mismatch": "GGUF architecture is not qwen35moe.",
    "layer_count_mismatch": "GGUF layer count did not match the expected 40 layers.",
    "embedding_width_mismatch": "GGUF embedding width did not match the expected 2048.",
    "expert_count_mismatch": "GGUF expert count did not match the expected 256.",
}


def unavailable_reason(engine):
    if supported(engine):
        return None
    if hasattr(engine, "alive") and not engine.alive():
        return "The engine is not currently loaded."
    if getattr(engine, "batch", 0):
        return REASONS["batching_enabled"]
    reason = (getattr(engine, "info", {}) or {}).get("json_schema_reason")
    return REASONS.get(reason, "The running executable did not advertise json_schema_llguidance. "
                       "Check the configured exe path, STRATA_POLY_LLGUIDANCE=ON, --no-draft, "
                       "and that --batch is absent; older binaries do not report the exact failed check.")


# Reject extension keywords before llguidance compilation: x-guidance can weaken schema semantics.
KEYWORDS = set("type properties required additionalProperties items prefixItems minItems maxItems "
               "enum const anyOf oneOf allOf $ref $defs definitions minimum maximum exclusiveMinimum "
               "exclusiveMaximum multipleOf minLength maxLength pattern format minProperties maxProperties "
               "patternProperties title description default examples deprecated readOnly writeOnly $schema $comment".split())


def check_keywords(schema):
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValueError("JSON Schema must be an object or boolean")
    if "$schema" in schema and schema["$schema"] not in (
            "https://json-schema.org/draft/2020-12/schema", "http://json-schema.org/draft/2020-12/schema"):
        raise ValueError("constrained decoding supports JSON Schema Draft 2020-12 only")
    for key, value in schema.items():
        if key not in KEYWORDS:
            raise ValueError(f"unsupported JSON Schema keyword: {key}")
        if key in ("properties", "$defs", "definitions", "patternProperties"):
            for child in value.values():
                check_keywords(child)
        elif key in ("anyOf", "oneOf", "allOf", "prefixItems"):
            for child in value:
                check_keywords(child)
        elif key in ("items", "additionalProperties"):
            check_keywords(value)


def write_request(file, tok, stop_ids, schema):
    specials = getattr(tok, "special_tokens", {})
    close = specials.get("</think>")
    tokens, types = getattr(tok, "tokens", ()), getattr(tok, "token_types", ())
    if close is None or not tokens or len(types or ()) != len(tokens) or not stop_ids:
        raise ValueError("structured decoding requires the complete Qwen tokenizer metadata")
    eos = sorted(stop_ids)
    if any(t < 0 or t >= len(tokens) for t in eos):
        raise ValueError("invalid structured decoding EOS mapping")
    file.write(struct.pack("<III", len(tokens), close, len(eos)))
    file.write(struct.pack("<" + "I" * len(eos), *eos))
    for t in range(len(tokens)):
        piece = tok.token_bytes(t) if types[t] == 1 else b""
        if types[t] == 1 and not piece:
            raise ValueError("empty ordinary tokenizer token")
        file.write(struct.pack("<I", len(piece)))
        file.write(piece)
    file.write(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    return close


@contextlib.contextmanager
def request(engine, tok, stop_ids, schema):
    if not supported(engine):
        raise ValueError("Structured output unavailable: " + unavailable_reason(engine))
    check_keywords(schema)
    executable = Path(engine.spawn[0])
    if not executable.is_absolute() and len(engine.spawn) > 2 and engine.spawn[2]:
        executable = Path(engine.spawn[2]) / executable
    checker = executable.resolve().with_name("poly-grammar-check")
    if not checker.is_file():
        raise StructuredOutputError("poly-grammar-check must be installed beside strata-poly")
    with tempfile.NamedTemporaryFile(prefix="poly-json-", suffix=".bin") as file:
        try:
            close = write_request(file, tok, stop_ids, schema)
        except (ValueError, TypeError, IndexError) as exc:
            raise StructuredOutputError(f"invalid grammar tokenizer mapping: {exc}") from exc
        file.flush()
        try:
            result = subprocess.run([str(checker), file.name], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StructuredOutputError(f"grammar preflight failed: {exc}") from exc
        if result.returncode:
            raise ValueError("JSON Schema grammar compilation failed: " + result.stderr.strip())
        yield file.name, close
