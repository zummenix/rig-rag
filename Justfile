# List available recipes.
default:
    @just --list

# Format all workspace code.
fmt:
    cargo fmt --all

# Lint, verify formatting, and test the whole workspace.
check:
    cargo fmt --all --check
    cargo clippy --workspace --all-targets -- -D warnings
    cargo test --workspace
