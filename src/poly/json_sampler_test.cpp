// Compile the actual production sampler and generation entry point without linking a GPU engine.
#define main poly_unused_main
#include "main.cpp"
#undef main
#include <cassert>
#include "grammar_token_bytes.hpp"
namespace strata::poly {
int most_likely(const float* logits, int n) {
    return int(std::max_element(logits, logits + n) - logits);
}
}
int main() {
    int next=256;
    for(int b=0;b<256;++b) {
        bool direct=(b>=33 && b<=126) || (b>=161 && b<=172) || b>=174;
        unsigned cp=direct ? b : next++;
        std::string encoded;
        if(cp<128) encoded+=char(cp);
        else { encoded+=char(192|(cp>>6)); encoded+=char(128|(cp&63)); }
        assert(strata::poly::grammar_token_bytes(encoded,1)==std::string(1,char(b)));
    }
    assert(strata::poly::grammar_token_bytes("</think>",4).empty());
    bool rejected=false;
    try { strata::poly::grammar_token_bytes("😀",1); } catch(const std::exception&) { rejected=true; }
    assert(rejected);

    std::vector<std::string> pieces;
    for (int i=0;i<256;++i) pieces.emplace_back(1,char(i));
    pieces.emplace_back(); pieces.emplace_back();
    for (int top_k : {0, 1, 20}) for (float temperature : {0.f, 0.7f, 2.f}) {
        strata::poly::JsonGrammar grammar(pieces, {256}, 257, false,
            R"({"type":"object","properties":{"x":{"const":1}},"required":["x"],"additionalProperties":false})");
        Sampling s; s.temperature=temperature; s.top_k=top_k; s.top_p=0.2; s.min_p=0.1;
        s.penalty_repeat=1.5; s.penalty_freq=0.3; s.penalty_present=0.2;
        Sampler sampler; std::mt19937_64 rng(7); std::vector<int32_t> history;
        std::vector<float> logits(pieces.size(), 1e6f);
        for(unsigned char expected : std::string("{\"x\":1}")) {
            // Give the fixture token the largest score among the permitted set. Disallowed tokens
            // remain arbitrarily high and must never make top-k, top-p or the final draw.
            const auto before=logits;
            auto bits=grammar.mask(logits.data());
            assert(logits==before);
            for(size_t i=0;i<logits.size();++i) if(std::isfinite(bits[i])) logits[i]=-10;
            logits[expected]=100;
            const int t=sampler.pick(grammar.mask(logits.data()), logits.size(), s, history, rng, true);
            assert(t==expected); grammar.accept(t); history.push_back(t);
            std::fill(logits.begin(),logits.end(),1e6f);
        }
        assert(grammar.complete());
    }
}
