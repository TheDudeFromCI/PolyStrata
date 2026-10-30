#!/usr/bin/env bash
# Build only; never calls setup.py/setup.sh, downloads weights, or starts a model.
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
LLG_REV=3d698ee07b3a67b0aef50fb6058612e8fd30aca2
LLAMA_REV=3cf03257f219afbe7334045ff7c6a06ac68c627d
RUST_MIN_VERSION=1.95.0
JOBS=4
ARCHS=${STRATA_CUDA_ARCHITECTURES:-86;120}
MODE=cuda
BUILD_DIR=
TEMP_CHECKOUT=

usage() {
    cat <<'EOF'
Usage: ./build.sh [--jobs N] [--arch '86;120'] [--build-dir PATH]
       ./build.sh --test [--jobs N] [--build-dir PATH]
       ./build.sh --fetch-only

Default: build Linux/CUDA strata-poly and poly-grammar-check in build-json/.
GPU defaults: RTX 3060 (86) and RTX 5060 (120; requires CUDA 12.8+).
--test: build/run native grammar tests and focused Python tests without CUDA/models.
--fetch-only: fetch pinned llguidance and llama.cpp source; do not install/build.
Requires Git, Python 3.10+ with venv/pip, a C++ compiler, and Rust/rustup.
CUDA mode additionally requires an installed CUDA toolkit. No sudo is used.
EOF
}
fail() { printf 'build.sh: %s\n' "$*" >&2; exit 1; }
while (($#)); do
    case $1 in
        --help|-h) usage; exit 0 ;;
        --test) [[ $MODE == cuda ]] || fail 'Choose only one mode'; MODE=test; shift ;;
        --fetch-only) [[ $MODE == cuda ]] || fail 'Choose only one mode'; MODE=fetch; shift ;;
        --jobs|--arch|--build-dir)
            (($# >= 2)) || fail "Missing value for $1"
            case $1 in --jobs) JOBS=$2 ;; --arch) ARCHS=$2 ;; --build-dir) BUILD_DIR=$2 ;; esac
            shift 2 ;;
        *) fail "Unknown argument: $1 (use --help)" ;;
    esac
done
[[ $(uname -s) == Linux ]] || fail 'This script supports Linux only'
[[ $JOBS =~ ^[1-9][0-9]*$ ]] || fail '--jobs must be a positive integer'
[[ $ARCHS =~ ^[0-9]+(\;[0-9]+)*$ ]] || fail "--arch must be a quoted list such as '86;120'"
command -v git >/dev/null || fail 'Install Git first'

# Check required system tools before installing anything or fetching sources.
if [[ $MODE != fetch ]]; then
    command -v python3 >/dev/null || fail 'Install Python 3.10+ with venv and pip'
    python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"'
    command -v c++ >/dev/null || fail 'Install a C++20 compiler (and CUDA-compatible host compiler for CUDA builds)'
    if [[ $MODE == cuda ]]; then
        NVCC=${CUDACXX:-}
        if [[ -z $NVCC ]]; then
            NVCC=$(command -v nvcc || true)
            for candidate in /opt/cuda/bin/nvcc /usr/local/cuda/bin/nvcc; do
                [[ -n $NVCC || ! -x $candidate ]] || NVCC=$candidate
            done
        fi
        [[ -n $NVCC ]] && command -v "$NVCC" >/dev/null || fail 'CUDA compiler not found. Install the CUDA toolkit or set CUDACXX; use --test for CPU-only verification.'
        version=$("$NVCC" --version)
        if [[ ";$ARCHS;" == *';120;'* && $version =~ release[[:space:]]+([0-9]+)\.([0-9]+) ]]; then
            major=${BASH_REMATCH[1]}; minor=${BASH_REMATCH[2]}
            ((major > 12 || (major == 12 && minor >= 8))) || fail "Architecture 120 requires CUDA 12.8+; choose --arch '86' only if building solely for the RTX 3060."
        fi
    fi
    for cargo_bin in "${CARGO_HOME:-$HOME/.cargo}/bin" "$HOME/.cargo/bin"; do
        [[ ! -d $cargo_bin ]] || export PATH="$cargo_bin:$PATH"
    done
    command -v cargo >/dev/null || fail 'Install Rust with Cargo (https://rustup.rs), then rerun'
fi

cleanup() { [[ -z $TEMP_CHECKOUT ]] || rm -rf -- "$TEMP_CHECKOUT"; }
trap cleanup EXIT
fetch_source() {
    local name=$1 remote=$2 revision=$3 destination="$ROOT/third_party/$1"
    if [[ -e $destination || -L $destination ]]; then
        [[ -d $destination/.git || -f $destination/.git ]] || fail "$destination exists but is not a Git checkout; move it aside yourself first"
        [[ $(git -C "$destination" rev-parse HEAD) == "$revision" ]] || fail "$destination is not pinned at $revision; preserve your work and move it aside before retrying"
        [[ -z $(git -C "$destination" status --porcelain --untracked-files=normal) ]] || fail "$destination has local changes; preserve them and move it aside before retrying"
        printf 'Using %s at %s\n' "$name" "$revision"
        return
    fi
    mkdir -p -- "$ROOT/third_party"
    TEMP_CHECKOUT=$(mktemp -d "$ROOT/third_party/.${name}-fetch.XXXXXX")
    git -C "$TEMP_CHECKOUT" init --quiet
    git -C "$TEMP_CHECKOUT" remote add origin "$remote"
    git -C "$TEMP_CHECKOUT" fetch --depth 1 origin "$revision"
    git -C "$TEMP_CHECKOUT" checkout --quiet --detach FETCH_HEAD
    [[ $(git -C "$TEMP_CHECKOUT" rev-parse HEAD) == "$revision" ]] || fail "Wrong revision downloaded for $name"
    mv -- "$TEMP_CHECKOUT" "$destination"
    TEMP_CHECKOUT=
}
fetch_source llguidance https://github.com/guidance-ai/llguidance.git "$LLG_REV"
fetch_source llama.cpp https://github.com/ggml-org/llama.cpp.git "$LLAMA_REV"
[[ $MODE != fetch ]] || { printf 'Pinned source dependencies are ready. No model files downloaded.\n'; exit 0; }

# Use a suitable active compiler (including newer releases) rather than downgrade it.
source "$ROOT/tools/build_rust_toolchain.sh"
select_build_rust_toolchain "$RUST_MIN_VERSION" || fail "Could not select Rust/Cargo $RUST_MIN_VERSION or newer"

if [[ ! -e $ROOT/.venv ]]; then
    python3 -m venv "$ROOT/.venv"
fi
[[ -x $ROOT/.venv/bin/python ]] || fail 'Existing .venv is incomplete; repair it before retrying'
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements-grammar.txt"
export PATH="$ROOT/.venv/bin:$PATH"

if [[ -z $BUILD_DIR ]]; then
    if [[ $MODE == test ]]; then BUILD_DIR=build-json-tests; else BUILD_DIR=build-json; fi
fi
[[ $BUILD_DIR == /* ]] || BUILD_DIR="$ROOT/$BUILD_DIR"
options=(-S "$ROOT" -B "$BUILD_DIR" -G Ninja -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_MAKE_PROGRAM="$ROOT/.venv/bin/ninja"
    -DSTRATA_ENABLE_HIP=OFF -DSTRATA_ENABLE_SYCL=OFF -DSTRATA_HIP_GFX906=OFF
    -DSTRATA_POLY_LLGUIDANCE=ON -DLLGUIDANCE_SOURCE_DIR="$ROOT/third_party/llguidance"
    -DSTRATA_GGML_DIR="$ROOT/third_party/llama.cpp")
if [[ $MODE == test ]]; then
    "$ROOT/.venv/bin/python" -m pip install pytest==9.2.0
    cmake "${options[@]}" -DSTRATA_ENABLE_CUDA=OFF -DSTRATA_NATIVE_EXPERTS=OFF -DSTRATA_BUILD_TESTS=ON
    cmake --build "$BUILD_DIR" --parallel "$JOBS" --target poly-grammar-check poly-json-grammar-test poly-json-sampler-test poly-json-draft-test
    ctest --test-dir "$BUILD_DIR" -R '^poly-json-(sampler|draft)-test$' --output-on-failure
    cd -- "$ROOT"
    POLY_GRAMMAR_TEST="$BUILD_DIR/poly-json-grammar-test" \
    POLY_GRAMMAR_CHECK="$BUILD_DIR/poly-grammar-check" \
        python -m pytest -q serve/test_poly_grammar.py serve/test_structured.py serve/test_effort_paths.py serve/test_server_startup.py
else
    cmake "${options[@]}" -DSTRATA_ENABLE_CUDA=ON -DSTRATA_NATIVE_EXPERTS=ON -DSTRATA_BUILD_TESTS=OFF \
        "-DCMAKE_CUDA_COMPILER=$NVCC" "-DCMAKE_CUDA_ARCHITECTURES=$ARCHS"
    cmake --build "$BUILD_DIR" --parallel "$JOBS" --target strata-poly poly-grammar-check
    printf '\nBuilt engine: %s/strata-poly\nGrammar helper: %s/poly-grammar-check\n' "$BUILD_DIR" "$BUILD_DIR"
    printf 'Select this engine in your model config. Remove --no-draft to enable MTP; keep batching off.\n'
fi
printf 'Done. No model downloads or inference were performed.\n'
