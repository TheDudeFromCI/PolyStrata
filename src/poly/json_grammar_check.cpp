#include "json_grammar.hpp"
#include <cstdio>
int main(int argc, char** argv) {
    try { if(argc != 2) return 2; strata::poly::GrammarInput in(argv[1]);
        strata::poly::JsonGrammar g(in.pieces, in.eos, in.close, false, in.schema, true); return 0;
    } catch(const std::exception& e) { std::fprintf(stderr, "%s\n", e.what()); return 1; }
}
