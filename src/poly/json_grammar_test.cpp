// Model-free fixture driver: high-logit invalid tokens must lose before sampling.
#include "json_grammar.hpp"
#include "grammar_metadata_fixture.hpp"
#include <iostream>
int main(int argc, char** argv) {
    try {
        if (argc < 3) return 2;
        strata::poly::GrammarInput in(argv[1]);
        auto metadata = strata::poly::fixture::metadata(in.pieces, in.close, std::string(argv[2]) != "metadata_sample");
        strata::poly::validate_grammar_metadata(&metadata.first, &metadata.second, in.pieces.size(), in.pieces, in.close);
        bool reasoning = std::string(argv[2]) == "reasoning";
        strata::poly::JsonGrammar g(in.pieces, in.eos, in.close, reasoning, in.schema);
        std::vector<float> logits(in.pieces.size(), -100.f);
        for (int t; std::cin >> t;) {
            if(t < 0 || size_t(t) >= logits.size()) throw std::runtime_error("bad fixture token");
            std::fill(logits.begin(), logits.end(), -100.f);
            // Every impossible candidate, including EOS, receives a much larger score than the fixture.
            logits[t] = 1.f;
            if (std::string(argv[2]) == "nonfinite") std::fill(logits.begin(), logits.end(), -INFINITY);
            const float* mask = g.mask(logits.data());
            if (g.reasoning() && mask != logits.data()) throw std::runtime_error("reasoning was masked");
            if (!std::isfinite(mask[t])) throw std::runtime_error("fixture token was rejected");
            if (!g.reasoning()) {
                for(size_t i=0;i<logits.size();++i) if(!std::isfinite(mask[i])) logits[i] = 1000000.f;
                mask = g.mask(logits.data());
                if(std::max_element(mask, mask+logits.size())-mask != t)
                    throw std::runtime_error("invalid token beat allowed fixture token");
            }
            g.accept(t);
        }
        if(!g.complete()) throw std::runtime_error("structured_output_failed: incomplete output");
        std::puts("complete");
    } catch(const std::exception& e) { std::fprintf(stderr,"%s\n",e.what()); return 1; }
}
