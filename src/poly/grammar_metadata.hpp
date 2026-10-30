#pragma once
#include "grammar_token_bytes.hpp"
#include "strata/artifact/gguf_reader.hpp"
#include <vector>
namespace strata::poly {
inline void validate_grammar_metadata(const MetaValue* tokens, const MetaValue* types,
                                     size_t n_vocab, const std::vector<std::string>& pieces, int close) {
    if (!tokens || !types || tokens->type != MetaType::ARRAY || types->type != MetaType::ARRAY ||
        tokens->elem != MetaType::STRING || (types->elem != MetaType::I32 && types->elem != MetaType::U32) ||
        tokens->count != pieces.size() || types->count != pieces.size() ||
        tokens->items.size() != pieces.size() || types->items.size() != pieces.size() || n_vocab != pieces.size()) {
        auto detail = [](const MetaValue* v) {
            return v ? "count=" + std::to_string(v->count) + ",retained=" + std::to_string(v->items.size()) +
                       ",element_type=" + std::to_string(static_cast<int>(v->elem)) : "missing";
        };
        throw std::runtime_error("grammar tokenizer metadata mismatch: request_vocab=" + std::to_string(pieces.size()) +
            ", model_vocab=" + std::to_string(n_vocab) + ", tokens={" + detail(tokens) + "}, types={" + detail(types) + "}");
    }
    if (close < 0 || size_t(close) >= pieces.size() || tokens->items[close].s != "</think>")
        throw std::runtime_error("invalid reasoning transition token");
    for (size_t i=0;i<pieces.size();++i) {
        if (grammar_token_bytes(tokens->items[i].s, types->items[i].u) != pieces[i])
            throw std::runtime_error("grammar tokenizer bytes differ from model at ID " + std::to_string(i));
    }
}
}
