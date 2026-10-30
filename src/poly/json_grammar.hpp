#pragma once
#include <llguidance.h>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace strata::poly {
// Model-independent, tested with synthetic vocabularies. No text decoding or UTF-8 repair here.
class JsonGrammar {
    struct TokFree { void operator()(LlgTokenizer* p) const { llg_free_tokenizer(p); } };
    struct MatchFree { void operator()(LlgMatcher* p) const { llg_free_matcher(p); } };
    std::unique_ptr<LlgTokenizer, TokFree> tok_;
    std::unique_ptr<LlgMatcher, MatchFree> matcher_;
    std::vector<float> masked_;
    std::vector<uint32_t> eos_;
    int close_;
    bool reasoning_, complete_ = false;
    void check(int rc) {
        if (rc < 0 || llg_matcher_is_error(matcher_.get()))
            throw std::runtime_error(std::string("structured_output_failed: ") +
                (llg_matcher_get_error(matcher_.get()) ? llg_matcher_get_error(matcher_.get()) : "grammar execution failed"));
    }
public:
    JsonGrammar(const std::vector<std::string>& pieces, const std::vector<uint32_t>& eos,
                int close, bool reasoning, const std::string& schema, bool preflight = false)
        : masked_(pieces.size()), eos_(eos), close_(close), reasoning_(reasoning) {
        if (eos.empty() || close < 0 || size_t(close) >= pieces.size())
            throw std::runtime_error("invalid grammar tokenizer special IDs");
        std::vector<uint32_t> lens;
        std::string bytes;
        for (const auto& p : pieces) { lens.push_back(p.size()); bytes += p; }
        for (auto e : eos) if (e >= pieces.size()) throw std::runtime_error("invalid grammar EOS");
        LlgTokenizerInitV2 ti{};
        ti.struct_size = sizeof(ti); ti.vocab_size = pieces.size(); ti.tok_eos = eos[0];
        ti.tok_eos_extra = eos.data() + 1; ti.tok_eos_extra_count = eos.size() - 1;
        ti.token_lens = lens.data(); ti.token_bytes = reinterpret_cast<const uint8_t*>(bytes.data());
        // Matcher never requests fast-forward tokens or retokenization. Only the exact byte trie is used.
        ti.use_approximate_greedy_tokenize_fn = true;
        char err[2048]{};
        tok_.reset(llg_new_tokenizer_v2(&ti, err, sizeof(err)));
        if (!tok_) throw std::runtime_error(std::string("invalid grammar tokenizer: ") + err);
        LlgConstraintInit ci{}; llg_constraint_init_set_defaults(&ci, tok_.get());
        ci.limits.verbose_errors = false;
        // Warnings, as well as errors, fail closed (no approximate schema semantics).
        if (preflight) {
            if (llg_validate_grammar(&ci, "json_schema", schema.c_str(), err, sizeof(err)) != 0)
                throw std::runtime_error(std::string("unsupported JSON Schema: ") + err);
            return;
        }
        matcher_.reset(llg_new_matcher(&ci, "json_schema", schema.c_str())); check(0);
    }
    bool reasoning() const { return reasoning_; }
    bool complete() const { return complete_; }
    const float* mask(const float* logits) {
        if (reasoning_) return logits;
        check(llg_matcher_compute_mask(matcher_.get()));
        auto bits = llg_matcher_get_mask(matcher_.get());
        bool any = false;
        for (size_t i = 0; i < masked_.size(); ++i) {
            const bool allowed = bits && (bits[i / 32] & (uint32_t(1) << (i % 32))) &&
                std::find(eos_.begin(), eos_.end(), i) == eos_.end();
            masked_[i] = allowed && std::isfinite(logits[i]) ? logits[i] : -INFINITY;
            any |= std::isfinite(masked_[i]);
        }
        if (!any) throw std::runtime_error("structured_output_failed: no valid next token");
        return masked_.data();
    }
    void accept(int t) {
        if (std::find(eos_.begin(), eos_.end(), t) != eos_.end())
            throw std::runtime_error("structured_output_failed: premature EOS");
        if (reasoning_) { if (t == close_) reasoning_ = false; return; }
        check(llg_matcher_consume_token(matcher_.get(), t));
        complete_ = llg_matcher_is_accepting(matcher_.get());
    }
};

// Private, local request file: little-endian u32 vocabulary count, close ID, EOS count,
// EOS IDs; then length-prefixed raw bytes of every token, followed by schema UTF-8.
struct GrammarInput {
    std::vector<std::string> pieces;
    std::vector<uint32_t> eos;
    int close;
    std::string schema;
    explicit GrammarInput(const std::string& path) {
        std::ifstream f(path, std::ios::binary);
        auto u32 = [&]() { unsigned char b[4]; if (!f.read((char*)b,4)) throw std::runtime_error("short grammar file");
            return uint32_t(b[0]) | uint32_t(b[1])<<8 | uint32_t(b[2])<<16 | uint32_t(b[3])<<24; };
        uint32_t n = u32(); close = u32(); uint32_t ne = u32();
        if (!n || n > 1000000 || !ne || ne > 32) throw std::runtime_error("invalid grammar vocabulary");
        for (uint32_t i=0;i<ne;++i) eos.push_back(u32());
        for (uint32_t i=0;i<n;++i) { auto len=u32(); if(len>1000000) throw std::runtime_error("oversized grammar token");
            std::string p(len, '\0'); if(!f.read(p.data(),len)) throw std::runtime_error("short grammar token"); pieces.push_back(std::move(p)); }
        schema.assign(std::istreambuf_iterator<char>(f), {});
        if (schema.empty() || schema.find('\0') != std::string::npos) throw std::runtime_error("invalid schema text");
    }
};
}
