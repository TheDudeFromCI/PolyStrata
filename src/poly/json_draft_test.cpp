// Exercise the production verifier and sampler with an actual llguidance matcher,
// synthetic logits and an engine-window/KV stand-in. Never loads model weights.
#define main poly_unused_main
#include "main.cpp"
#undef main
#include <cassert>
namespace strata::poly {
int most_likely(const float* logits, int n) {
    return int(std::max_element(logits,logits+n)-logits);
}
}

static void simulate(bool reasoning, int corrupt, int width, int budget, bool cancelled,
                     float temperature, bool unicode, bool long_reasoning = false) {
    std::vector<std::string> pieces;
    for(int i=0;i<256;++i) pieces.emplace_back(1,char(i));
    pieces.emplace_back(); pieces.emplace_back(); // EOS, </think>
    const std::string answer = unicode ? "{\"x\":\"é😀\\n\"}" : "{\"x\":1}";
    const std::string schema = unicode ? R"({"type":"object","properties":{"x":{"const":"é😀\n"}},"required":["x"],"additionalProperties":false})"
        : R"({"type":"object","properties":{"x":{"const":1}},"required":["x"],"additionalProperties":false})";
    strata::poly::JsonGrammar grammar(pieces,{256},257,reasoning,schema);
    std::vector<int32_t> expected;
    if(reasoning) {
        // Literal marker-like bytes and invalid JSON are ordinary reasoning.
        for(unsigned char c : std::string("code {bad JSON} </think> math!")) expected.push_back(c);
        if(long_reasoning) for(int i=0;i<10000;++i) expected.push_back(97+i%20);
        expected.push_back(257);
    }
    for(unsigned char c : answer) expected.push_back(c);
    Sampling settings; settings.temperature=temperature; settings.top_k=20; settings.top_p=.95f;
    settings.penalty_repeat=1.1f; settings.penalty_freq=.1f; settings.penalty_present=.1f;
    Sampler sampler; std::mt19937_64 rng(42);
    std::vector<int32_t> history,kv;
    std::vector<float> logits(pieces.size());
    int masks=0,accepted=0,rejected=0,windows=0;
    auto pick = [&]() {
        assert(history.size()<expected.size());
        std::fill(logits.begin(),logits.end(),-10000.f);
        logits[expected[history.size()]]=100.f;
        if(!grammar.reasoning()) { logits[256]=1e8f; ++masks; }
        const float* allowed=grammar.mask(logits.data());
        if(grammar.reasoning()) assert(allowed==logits.data());
        int t=sampler.pick(allowed,logits.size(),settings,history,rng,true);
        assert(t==expected[history.size()]);
        return t;
    };
    auto show = [&](int32_t t) {
        assert(history.size()<expected.size() && t==expected[history.size()]);
        grammar.accept(t); history.push_back(t);
        return !grammar.complete() && int(history.size())<budget;
    };
    int t=pick();
    while(show(t)) {
        const size_t start=kv.size();
        assert(history.size()==start+1);
        std::vector<int32_t> window{t};
        for(int i=1;i<width;++i) {
            size_t at=start+i;
            int candidate=at<expected.size() ? expected[at] : 256;
            if(int(at)==corrupt) candidate=256; // invalid draft (also tests premature EOS proposals)
            window.push_back(candidate);
        }
        kv.insert(kv.end(),window.begin(),window.end()); // speculative engine evaluation
        ++windows;
        auto verified=strata::poly::verify_draft_window(window.data(),window.size(),[&](int row) {
            // The target row must be conditioned on exactly the accepted prefix.
            assert(history.size()==start+row+1);
            for(int i=0;i<=row;++i) assert(window[i]==expected[start+i]);
            return pick();
        },show);
        accepted+=verified.accepted;
        rejected+=verified.more && verified.fed<int(window.size());
        kv.resize(start+verified.fed); // engine.keep(), rejected suffix must disappear
        assert(kv.size()<=history.size());
        assert(std::equal(kv.begin(),kv.end(),history.begin()));
        t=verified.next;
        if(!verified.more) break;
    }
    assert(history.size()==std::min(size_t(budget),expected.size()));
    assert(std::equal(history.begin(),history.end(),expected.begin()));
    assert(grammar.complete()==(history.size()==expected.size()));
    // Budget/cancel stops use the same false-return contract as production show().
    if(cancelled) assert(!grammar.complete());
    if(budget>1) assert(windows>0);
    if(width>1 && corrupt<0 && budget>2) assert(accepted>0);
    if(!reasoning) assert(masks>0);
    (void)rejected;
}
static void plain_verifier() {
    for(int count=1;count<=8;++count) for(int match=0;match<count;++match)
        for(int stop=-1;stop<match;++stop) {
            std::vector<int32_t> window;
            for(int i=0;i<count;++i) window.push_back(10+i);
            int emitted=0;
            auto result=strata::poly::verify_draft_window(window.data(),count,
                [&](int row) { return row<match ? window[row+1] : 999; },
                [&](int t) { assert(t==window[emitted+1]); return emitted++!=stop; });
            assert(result.more==(stop<0));
            assert(result.accepted==(stop<0 ? match : stop+1));
            assert(result.fed==(stop<0 ? match+1 : stop+1));
            assert(result.next==(stop<0 ? 999 : window[stop+1]));
        }
}
int main() {
    plain_verifier();
    // Exhaust every possible rejection position, including either side of </think>,
    // the final brace, Unicode byte splits, and the fully accepted case.
    for(bool reasoning : {false,true}) for(bool unicode : {false,true})
        for(int width : {1,2,4,8}) for(float temperature : {0.f,.7f})
            for(int corrupt=-1;corrupt<55;++corrupt)
                simulate(reasoning,corrupt,width,1000,false,temperature,unicode);
    for(int budget : {1,2,5,28,29,30,31,32,33,34})
        simulate(true,-1,8,budget,true,0.f,false);
    // Fresh matcher state on every run, alternating incompatible schemas/phases.
    simulate(false,-1,4,1000,false,0.f,true);
    simulate(true,-1,4,1000,false,0.f,false);
    simulate(true,-1,8,100000,false,.7f,false,true);
    std::puts("draft verification: all synthetic scenarios passed");
}
