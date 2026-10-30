# Grammar-verified drafts for Qwen3.6 strata-poly

Both MTP and lookup proposals can now accelerate structured requests. Remove
`--no-draft` from the active model configuration to restore MTP, rebuild the
llguidance-enabled `strata-poly`, and restart that executable. The loaded model
must already contain its draft block (or have its existing local draft-model
configuration). Nothing is downloaded by this feature. Batching remains off;
`--follow` remains unsupported for constrained requests.

Use the same build directory and flags as the JSON decoder:

```sh
cmake --build build-json --target strata-poly poly-grammar-check --parallel 4
```

After restart, `/v1/status.structured_output.draft_verification` should be true.
This reports support, not whether MTP is loaded, used, or accepting proposals in
a particular request. Engine request logs and draft acceptance statistics report
actual use. Keeping `--no-draft` is supported if single-token/MTP-off behavior is
wanted; lookup behavior follows its existing independent settings.

## Verification and state

`src/poly/draft_verify.hpp` extracts the engine's existing sample-and-compare
window verification loop without changing its sampling, accepted-prefix,
engine.keep() or policy accounting semantics for ordinary requests.

Draft tokens are tentative proposals and do not advance the grammar. At each
verification row the main-model logits are sampled through the ordinary sampler:
no grammar work during reasoning; llguidance mask before filtering/sampling in
the final answer. A proposed token is emitted only if it equals that target
sample. Only emission calls grammar.accept() and updates token history.

If a proposal is rejected, the remaining window is discarded through the existing
engine.keep() path, and the sampled replacement is pending for the next iteration.
Because unaccepted tokens never entered the matcher, no matcher cloning or grammar
rollback is needed. The retained KV prefix and committed grammar prefix stay
aligned. The terminal output token is emitted but not fed back into the engine,
matching the original generation loop.

An accepted actual </think> special token activates JSON masking before verifying
the next proposal, including within the same window. Literal marker-like text in
ordinary tokens does not change phases. Accepted root completion stops immediately,
discarding the rest of the window. Cancellation and token budgets use the same
stop contract. Premature target EOS still fails closed; proposed EOS cannot bypass
that check. Schema compilation, metadata checks and independent output validation
remain in place. There is no grammar cache or per-token Python matcher callback.

This uses the existing target sample-and-compare acceptance rule, not a new
probability-ratio speculative decoding algorithm. Invalid draft proposals are
safe to reject but can reduce acceptance and throughput. No recovery to the
previous 95 tok/s is promised: acceptance rate, available VRAM/expert residency
and grammar overhead must be measured on the target machine.

## Model-free tests

`poly-json-draft-test` uses the actual production verifier, sampler and llguidance
with synthetic logits and a speculative KV stand-in. It runs 1,805 scenarios
covering reasoning on/off, literal markers, Unicode byte splits, rejection
positions, widths 1/2/4/8, greedy/stochastic sampling, terminal tokens, budget and
cancel stop contracts, alternating fresh matchers and a 10,000-token reasoning
prefix. Another 120 cases verify unchanged plain-text window results. Existing
sampler tests continue to verify that arbitrarily high invalid logits are masked.

```sh
cmake --build build-json-tests --target poly-json-draft-test poly-json-sampler-test -j4
ctest --test-dir build-json-tests -R '^poly-json-(sampler|draft)-test$' --output-on-failure
```

No models are downloaded or executed. Full CUDA linking, real engine KV/MTP
execution and real-model performance remain unverified in this development
machine, which has no CUDA toolkit. The KV stand-in checks prefix/rollback
contracts, not GPU cache implementation internals.

Recorded results: both native CTests passed (draft verification about 3.2 seconds
on this CPU); 51 focused Python/API/startup tests and 75 subtests passed. The
broader regression run passed 353 existing server tests and 431 subtests, excluding
the previously reproduced baseline container-memory telemetry failure. The new
fake-process handshake test initially had an invocation error; it was corrected
and passed in the final focused run. Host syntax checks passed with the feature
on and off; no CUDA build or real-model speed claim is made.
