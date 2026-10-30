# Qwen3.6 structured responses with llguidance

This opt-in decoder targets Qwen3.6-35B-A3B, Linux, NVIDIA CUDA, `strata-poly`,
`/v1/chat/completions`, with batching off. Native MTP and lookup drafts are supported. It does not change `strata`.
No model files are needed for the tests below. Do not run setup.py or setup.sh for these tests.

## Build and deploy

Install the normal CUDA build prerequisites from docs/BACKENDS.md (on Manjaro:
the NVIDIA driver, CUDA toolkit, GCC supported by that toolkit, CMake, Ninja,
Python 3.10+). Install Rust 1.95.0 with Cargo. Then, from the project root:

```sh
git clone https://github.com/guidance-ai/llguidance.git third_party/llguidance
git -C third_party/llguidance checkout 3d698ee07b3a67b0aef50fb6058612e8fd30aca2
python3 -m venv .venv
.venv/bin/pip install -r requirements-grammar.txt pytest==9.2.0
cmake -S . -B build-json -G Ninja -DSTRATA_ENABLE_CUDA=ON \
  -DSTRATA_POLY_LLGUIDANCE=ON -DSTRATA_BUILD_TESTS=ON \
  -DLLGUIDANCE_SOURCE_DIR="$PWD/third_party/llguidance"
cmake --build build-json --target strata-poly poly-grammar-check poly-json-grammar-test poly-json-sampler-test poly-json-draft-test -j4
```

Use the existing Qwen3.6 model configuration, with `exe` pointing to the newly
built `strata-poly`. Remove `--no-draft` from `args` to enable MTP; keeping it
turns MTP off. Leave batching off. Install
`poly-grammar-check` beside `strata-poly` if moving the executables. This helper
validates schemas locally; it never loads a model. The Cargo lockfile and exact
source commit pin llguidance 1.9.1. A wrong source commit is a CMake error.

`/v1/status.structured_output.constrained_decoding` must be true and `method`
must be `llguidance` after loading this engine. Native READY advertises
`json_schema_llguidance` only for the supported model shape, with batching
off. Unsupported engines reject structured OpenAI requests, rather than falling
back to prompt-only generation. Ordinary text requests retain their previous path.

## Requests and reasoning

Use the standard OpenAI `response_format` with `type: json_schema`, a name, and
an object-root schema, or `type: json_object`. Pydantic AI's OpenAI provider and
`NativeOutput(MyPydanticModel)` send this format; no custom client settings are
needed. Set `reasoning_effort` to low, medium or high as before; explicit none
also works. `max_completion_tokens` counts reasoning and final tokens together.

The server exports the exact inference tokenizer's raw token bytes, without
UTF-8 replacement, to a private local request file. Before generating, the native
engine checks every byte against its loaded GGUF's GPT-2 byte alphabet and
special-token metadata. Special tokens are excluded from the JSON vocabulary.
The native C API builds one matcher per generation segment. Its trie matches
multi-character tokens, partial UTF-8 and JSON escapes across token boundaries.
No custom schema compiler is used. The matcher's no-fast-forward path uses the
byte trie only; llguidance's approximate encode callback is never used to generate
tokens or change token IDs.

While reasoning, logits are unrestricted and the matcher consumes no tokens.
Only the actual `</think>` special-token ID switches to the fresh JSON state.
Ordinary tokens spelling similar text do not switch phases. The API uses the
same token-ID transition and emits reasoning through `reasoning_content`; it
never feeds final JSON through the text-based reasoning/tool marker parser.
Existing reasoning budget wrap-up inserts its closing special token and resumes
with a fresh final-answer matcher. Premature EOS does not trigger the old
reasoning-close retry. Budget exhaustion before a complete answer is an error.

Every final token is sampled from grammar-masked CPU logits before penalties,
top-k, top-p, min-p, temperature and the draw. The original logits remain intact.
The engine stops at accepting root completion, without requiring an EOS draw.
MTP and lookup may propose tokens during both phases. Each proposal is checked
against a fresh main-model sample: unrestricted during reasoning, grammar-masked
in the final answer. Only emitted tokens advance grammar state. A rejected suffix
is discarded through the existing engine.keep() path; the matcher needs no rollback.
An accepted </think> activates masking for the very next token, even inside a window.
Completion, cancellation and budget stops discard the remainder of the window. No extra GPU transfer or per-token Python grammar
callback is introduced. Final content is buffered, parsed as exactly one JSON
object, and independently validated before delivery; prose extraction is disabled.

## Schemas and errors

The supported subset uses Draft 2020-12 and the pinned llguidance strict compiler:
objects/nested objects, properties and required fields, additionalProperties,
arrays/items, primitive types, enums/const, anyOf/nullable unions, local $ref and
$defs, string length/pattern/recognized format and numeric bounds/multipleOf.
Properties have schema-defined order. Some allOf/oneOf, property-count and
patternProperties combinations are unsupported; compilation errors and warnings
are client errors. Unsupported keywords (including uniqueItems, not,
unevaluatedProperties and x-guidance extensions) are rejected, never dropped.
Remote references are forbidden. This is not complete JSON Schema support.
llguidance does not enforce uniqueness of dynamically named properties; the
independent duplicate-key check rejects such an output rather than returning it.

Invalid schema/unsupported engine requests return 400 before generation. Native
mask/matcher/tokenizer errors use `structured_output_failed`; no valid token,
premature EOS, incomplete output or independent validation failure cannot return
a successful structured completion. Streaming may already have HTTP 200 headers;
failures then appear as error events without a successful content delta. Python
`jsonschema` is required for schema mode and may not silently degrade validation.

## Model-free verification

The grammar targets can be built without CUDA for local tests:

```sh
cmake -S . -B build-json-tests -G Ninja -DSTRATA_NATIVE_EXPERTS=OFF \
  -DSTRATA_POLY_LLGUIDANCE=ON -DSTRATA_BUILD_TESTS=ON \
  -DLLGUIDANCE_SOURCE_DIR="$PWD/third_party/llguidance"
cmake --build build-json-tests --target poly-grammar-check poly-json-grammar-test poly-json-sampler-test poly-json-draft-test -j4
ctest --test-dir build-json-tests -R '^poly-json-(sampler|draft)-test$' --output-on-failure
POLY_GRAMMAR_TEST="$PWD/build-json-tests/poly-json-grammar-test" \
POLY_GRAMMAR_CHECK="$PWD/build-json-tests/poly-grammar-check" \
  .venv/bin/python -m pytest -q serve/test_poly_grammar.py serve/test_structured.py serve/test_effort_paths.py
```

Fixtures cover Pydantic-shaped objects, arrays, nullable values, refs, Unicode,
escapes, numeric constraints, invalid high logits, EOS, incomplete prefixes,
matcher isolation, reasoning and HTTP buffering. The sampler test exercises the
actual production sampler with synthetic logits and penalties/sampling filters.
There are no model weights or GPU inference in these tests.

Per-token overhead is a native grammar mask plus one vocabulary pass into a
reusable float buffer. No mask work occurs during reasoning. Per-request costs
include vocabulary export/checking, schema preflight and tokenizer-trie creation;
these are not cached yet. Invalid draft proposals reduce acceptance and may limit
speculative speedups. The existing sample-and-compare verification policy is used;
this is not distribution-ratio speculative sampling. No
real-model throughput or reliability measurement is claimed. Complete CUDA
linking, GPU execution and the actual Qwen tokenizer/model combination require
verification on the target machine; the development test machine has no CUDA
compiler. Mock tests cannot establish real-model reliability.

Example client (using the current Pydantic AI OpenAI chat adapter):

```python
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

class Answer(BaseModel):
    success: bool
    count: int
    message: str

model = OpenAIChatModel(
    'qwen3.6-35b-a3b',
    provider=OpenAIProvider(base_url='http://127.0.0.1:8080/v1', api_key='unused'),
)
agent = Agent(model, output_type=NativeOutput(Answer))
result = agent.run_sync('Think through the count, then return the answer.')
print(result.output)
```

The native capability gate requires GGUF `general.name` to identify Qwen3.6 and
checks architecture, layer count, embedding width and expert count. Repacked
files with missing or unrelated names are rejected; no other model is advertised.
The mock tests do not verify a real client's installed Pydantic AI version.

## Results in the implementation environment

- Focused native-fixture, structured HTTP and reasoning-effort suite: 43 passed,
  75 subtests passed.
- Existing server regression suite: 353 passed, 431 subtests passed; one existing
  telemetry test excluded after reproducing its failure in the unchanged checkout.
  `TelemetryNoDisks.test_sampler_runs_without_disk_counters` expects mocked host
  RAM while the implementation's existing container-memory override reports cgroup RAM.
- CMake built `poly-grammar-check`, `poly-json-grammar-test` and
  `poly-json-sampler-test`; the production sampler CTest passed.
- `src/poly/main.cpp` passed host C++ syntax checks with the feature enabled and
  disabled. Python syntax checks and `git diff --check` passed.
- No model files were downloaded, installed, loaded or executed. Full CUDA
  compilation/linking and real-model inference remain unverified.

## Launcher configuration and diagnostics

`./setup.sh` runs the installer/launcher. It selects its saved `strata-*.json`
configuration and normally uses `engine-poly/strata-poly`. It does not select a
binary just because you built `build-json/strata-poly`. Its build path does not
enable the opt-in llguidance feature. For this custom build, set the active
configuration's `exe` to the absolute path of `build-json/strata-poly`, remove
`--no-draft` from its existing `args` list to enable MTP, and remove `--batch` and its value if
present. Keep the model path, layer split, context and memory settings.

Start that configuration directly, so installer engine selection/build logic
cannot replace the custom executable:

```sh
.venv/bin/python serve/server.py --engine strata --config /absolute/path/to/strata-YOUR-MODEL.json
```

Here `--engine strata` selects the existing Python native-process wrapper; the
configuration's `exe` selects the actual `strata-poly` executable.

With the diagnostic update, `/v1/status.structured_output.unavailable_reason`
reports the native gate that failed: missing compile option, batching, GGUF name,
architecture or shape. A drafts_enabled reason comes only from older native builds.
Older native executables cannot
report an exact gate; the server then says their READY line lacks the capability
flag. `/v1/status.structured_output.draft_verification` reports support for the
updated verifier. See [JSON_SCHEMA_DRAFTS.md](JSON_SCHEMA_DRAFTS.md). A one-request
concurrency value alone cannot diagnose draft generation or
which executable is running. Two GPUs appearing in telemetry is not a rejection
condition in this code.

## One-command build

After syncing the implementation branch, run `./build.sh` from the checkout.
It works from other working directories too. It fetches the exact llguidance and
llama.cpp source commits above into `third_party/`, creates/reuses `.venv`, installs
`requirements-grammar.txt` (including CMake/Ninja), selects Rust 1.95.0 through
rustup when available, and builds `strata-poly` plus `poly-grammar-check` together
in `build-json/`. Existing conflicting or modified dependency checkouts are
reported instead of reset. Source fetches use normal Git/TLS authentication.

System prerequisites: Git, Python 3.10+ with venv/pip, a C++20 compiler compatible
with your CUDA toolkit, Cargo (normally installed via rustup), and CUDA. The
script does not install drivers, system packages or rustup, and never calls the
model installer, downloads weights, launches inference, or edits model configs.
The defaults target your RTX 3060 and RTX 5060 (`86;120`, requiring CUDA 12.8+).

```sh
./build.sh                          # default CUDA engine/helper build, 4 jobs
./build.sh --jobs 2                  # reduce compiler memory use
./build.sh --arch '86'               # build solely for an RTX 3060
./build.sh --fetch-only              # source dependencies only
./build.sh --test                    # CPU-only native + mock API verification
```

`--build-dir PATH` overrides the build directory. Test mode defaults to
`build-json-tests/` and installs pytest 9.2.0. `STRATA_CUDA_ARCHITECTURES` can
supply the default architecture list; `CUDACXX` can select an nvcc executable.
If CUDA is not on PATH, `/opt/cuda/bin/nvcc` and `/usr/local/cuda/bin/nvcc` are
also checked. Stop the running server before rebuilding its executable, then
restart using the custom-build configuration described above. MTP remains
controlled by the configuration's existing `--no-draft` argument.
