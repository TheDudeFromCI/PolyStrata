#pragma once
#include <array>
#include <stdexcept>
#include <string>
namespace strata::poly {
// The Qwen GPT-2 byte alphabet, inverse of tools/strata_tokenizer.py's bytes_to_unicode.
inline std::string grammar_token_bytes(const std::string& raw, int type) {
    if (type != 1) return {};  // control, user-defined and unused tokens cannot appear in JSON
    static const auto inverse = [] {
        std::array<int,512> table; table.fill(-1);
        int next=256;
        for(int b=0;b<256;++b) {
            bool direct=(b>=33 && b<=126) || (b>=161 && b<=172) || b>=174;
            table[direct ? b : next++]=b;
        }
        return table;
    }();
    std::string decoded;
    for(size_t j=0;j<raw.size();) {
        unsigned c=(unsigned char)raw[j++];
        if(c>=128) {
            if(c<192 || c>=224 || j==raw.size()) throw std::runtime_error("invalid GPT2 byte alphabet");
            unsigned d=(unsigned char)raw[j++];
            if((d&192)!=128 || c < 194) throw std::runtime_error("invalid GPT2 byte alphabet");
            c=((c&31)<<6)|(d&63);
        }
        if(c>=inverse.size() || inverse[c]<0) throw std::runtime_error("invalid GPT2 byte alphabet");
        decoded+=char(inverse[c]);
    }
    if(decoded.empty()) throw std::runtime_error("empty ordinary grammar token");
    return decoded;
}
}
