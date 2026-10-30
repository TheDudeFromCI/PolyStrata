# Sourced by build.sh; no work is performed merely by sourcing this file.
rust_build_version_at_least() {
    local output=$1 minimum=$2 required_major required_minor required_patch
    [[ $output =~ ^(rustc|cargo)[[:space:]]+([0-9]+)\.([0-9]+)\.([0-9]+)([[:space:]]|$) ]] || return 1
    local major=${BASH_REMATCH[2]} minor=${BASH_REMATCH[3]} patch=${BASH_REMATCH[4]}
    IFS=. read -r required_major required_minor required_patch <<< "$minimum"
    ((10#$major > 10#$required_major ||
      (10#$major == 10#$required_major && 10#$minor > 10#$required_minor) ||
      (10#$major == 10#$required_major && 10#$minor == 10#$required_minor && 10#$patch >= 10#$required_patch)))
}

select_build_rust_toolchain() {
    local minimum=${1:-1.95.0} rust_version cargo_version active
    rust_version=$(rustc --version 2>/dev/null) || rust_version=
    cargo_version=$(cargo --version 2>/dev/null) || cargo_version=
    if rust_build_version_at_least "$rust_version" "$minimum" &&
       rust_build_version_at_least "$cargo_version" "$minimum"; then
        # Preserve rustup's selected toolchain when Cargo runs in CMake's build directory.
        if command -v rustup >/dev/null && active=$(rustup show active-toolchain 2>/dev/null); then
            read -r active _ <<< "$active"
            export RUSTUP_TOOLCHAIN="$active"
        fi
    elif command -v rustup >/dev/null; then
        rustup toolchain install "$minimum" --profile minimal || return 1
        export RUSTUP_TOOLCHAIN="$minimum"
        rust_version=$(rustc --version) || return 1
        cargo_version=$(cargo --version) || return 1
    else
        printf 'Rust and Cargo %s or newer are required; install/update them via rustup.\n' "$minimum" >&2
        return 1
    fi
    rust_build_version_at_least "$rust_version" "$minimum" &&
        rust_build_version_at_least "$cargo_version" "$minimum" || return 1
    printf 'Using %s; %s\n' "$rust_version" "$cargo_version"
}
