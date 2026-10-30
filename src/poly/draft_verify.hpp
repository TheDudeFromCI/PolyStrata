#pragma once
#include <cstdint>
namespace strata::poly {
struct VerifiedDraft {
    int32_t next;  // sampled target token; pending unless show() stopped on it
    int fed;       // input tokens to retain in the engine (terminal output is not fed)
    int accepted;  // proposed tokens actually emitted
    bool more;
};
// The grammar lives exclusively in pick()/show() on the committed prefix. Proposed
// tokens are never consumed by it. A rejected suffix needs engine rollback only.
// This is the existing sample-and-compare verifier, not distribution-ratio sampling.
template<class Pick, class Show>
VerifiedDraft verify_draft_window(const int32_t* window, int count, Pick pick, Show show) {
    int right = 0;
    bool more = true;
    int32_t t = pick(0);
    while (right < count - 1 && t == window[right + 1]) {
        if (!(more = show(t))) break;
        t = pick(++right);
    }
    return {t, right + 1, right + (more ? 0 : 1), more};
}
}
