#pragma once
// Synthetic metadata bytes only: no GGUF file, tensors or model weights.
#include "grammar_metadata.hpp"
namespace strata::poly::fixture {
inline void word(std::string& out, uint64_t value, int n) {
    for(int i=0;i<n;++i) out+=char(value>>(8*i));
}
inline std::string encode_bytes(const std::string& bytes) {
    std::string raw;
    for(unsigned char b : bytes) {
        int cp=b;
        if(!((b>=33 && b<=126) || (b>=161 && b<=172) || b>=174)) {
            cp=256;
            for(int x=0;x<b;++x) if(!((x>=33 && x<=126) || (x>=161 && x<=172) || x>=174)) ++cp;
        }
        if(cp<128) raw+=char(cp);
        else { raw+=char(192|(cp>>6)); raw+=char(128|(cp&63)); }
    }
    return raw;
}
inline std::pair<MetaValue,MetaValue> metadata(const std::vector<std::string>& pieces, int close, bool retain=true) {
    std::string strings,types;
    word(strings,uint32_t(MetaType::STRING),4); word(strings,pieces.size(),8);
    word(types,uint32_t(MetaType::I32),4); word(types,pieces.size(),8);
    for(size_t i=0;i<pieces.size();++i) {
        auto raw=int(i)==close ? std::string("</think>") : encode_bytes(pieces[i]);
        word(strings,raw.size(),8); strings+=raw;
        word(types,pieces[i].empty() ? 4 : 1,4);
    }
    Cursor sc(reinterpret_cast<const uint8_t*>(strings.data()),strings.size());
    Cursor tc(reinterpret_cast<const uint8_t*>(types.data()),types.size());
    return {read_value(sc,MetaType::ARRAY,0,retain),read_value(tc,MetaType::ARRAY,0,retain)};
}
}
