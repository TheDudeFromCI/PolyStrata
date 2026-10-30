# Tokenizer metadata mismatch diagnosis

The grammar library is llguidance 1.9.1, pinned to
`3d698ee07b3a67b0aef50fb6058612e8fd30aca2`. The reported message originated in
PolyStrata's `Engine::validate_grammar_tokens()` in `src/poly/engine.cpp`, before
matcher creation or inference. It was not a llguidance error or a client error.
The HTTP streaming headers were already sent when native generation began, so
the server delivered this failure as a streaming error event. Python schema
preflight cannot detect sampled native GGUF metadata; it only sees its exported
request tokenizer.

## Root cause and field values

`include/strata/artifact/gguf_reader.hpp` deliberately samples metadata arrays:
`MetaValue.count` records their complete length, but `MetaValue.items` retains
only the first 64 elements. The original grammar integration incorrectly treated
`items` as the complete tokenizer arrays.

For a valid vocabulary of N > 64 tokens, the expected comparisons are:

| Field | Required value | Old reader representation |
| --- | --- | --- |
| Request vocabulary (`GrammarInput.pieces.size()`) | N | N |
| Loaded engine vocabulary (`n_vocab_`) | N | N |
| `tokenizer.ggml.tokens.count` | N | N |
| `tokenizer.ggml.tokens.items.size()` | N | 64 |
| `tokenizer.ggml.token_type.count` | N | N |
| `tokenizer.ggml.token_type.items.size()` | N | 64 |

The user's original error did not include the actual N or distinguish absent
metadata from count differences. We cannot recover that number from the supplied
error. The synthetic reproduction uses N=260, and reports expected vocab=260,
model vocab=260, declared token/type counts=260, retained token/type entries=64.
The fix reports all these counts and element types on subsequent mismatches.

The shared GGUF reader now has an opt-in full-retention path for exactly the two
tokenizer arrays. Only the grammar-enabled `strata-poly` primary shard enables
it. Other arrays, other shards and the alternative `strata` engine retain the
existing default sampling behavior. The validator still checks complete declared
and retained counts against the active engine vocabulary, the actual close-marker
ID, and every decoded token byte, including IDs above 63. Genuine mismatches still
fail; no padding, truncation, bypass or unconstrained fallback was added.

## Active tokenizer and cache audit

- Python exports `Service.tok`, the active tokenizer used for prompt encoding and
  output detokenization. Its byte cache is per tokenizer instance.
- The private request file carries those token bytes, EOS and close IDs and schema
  text. It does not serialize a compiled grammar or matcher state.
- `poly-grammar-check` compiles/validates the schema using this same file.
- The native generation path reads the same file and compares it with the GGUF
  metadata of its loaded `Engine`. After validation it constructs llguidance's
  tokenizer and matcher from those verified bytes, and masks the engine's logits.
- There is no compiled-grammar cache or matcher cache. Each native generation
  segment constructs fresh state. Neither schema changes nor warm conversation
  caches cause grammar reuse across tokenizers. The static GPT-2 byte-alphabet
  inverse is model-independent, not a cached tokenizer or grammar.
- A model/configuration race would remain a validation failure if token bytes or
  vocabulary dimensions differ; it cannot bypass the check. This report does not
  assert runtime access to the user's tokenizer files or GPU caches.

## Verification without models

CMake built the native fixture and production-sampler tests; the sampler CTest
passed. Synthetic GGUF **metadata-array bytes in memory**, with no GGUF files,
tensors or weights, exercise the actual C++ `read_value()` code. Tests reproduce
the 64-entry sampling bug, pass with complete arrays, preserve default sampling,
and reject a modified token at ID 100 after full retention.

Direct streaming HTTP requests to `/v1/chat/completions` use `strict: true` and
execute the real native metadata validator and llguidance mask/consume loop with
synthetic logits through the fixture inference boundary. One test starts a fresh
service with a simple schema. Another starts a fresh service and sends A, A, B,
A, B, B, A, reusing the same server/tokenizer after the first request. Every final
JSON object passes independent jsonschema validation. No Trajectory Remix client
is involved. No compiled-grammar cache exists to prewarm; the repeated requests
exercise warm service/prompt state and fresh per-request grammars.

Focused suite: **50 passed, 75 subtests passed** (grammar, metadata, API, schema,
reasoning and startup tests). Native sampler CTest: **passed**. `main.cpp` host
syntax checking and `git diff --check`: **passed**.

No models were downloaded or run. Actual GPU KV-cache behavior, the user's exact
GGUF values and full CUDA linking remain unverified on this machine.

## Updating

Rebuild the grammar-enabled `strata-poly` executable and restart the server with
that executable selected in the active configuration. Rebuilding only the Python
server or `poly-grammar-check` does not fix the loaded engine's metadata retention.
No model changes, downloads or removal of cache files are needed.
