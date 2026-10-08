# List available recipes.
default:
    @just --list

# Format all workspace code.
fmt:
    cargo fmt --all

# Lint, verify formatting, and test the whole workspace.
check:
    cargo fmt --all --check
    cargo clippy --workspace --all-targets --all-features -- -D warnings
    cargo test --workspace --all-features

# Run the Playwright UI e2e suite (one-time: npm install + `playwright install chromium`).
test-site:
    cd site/tests && npm install && npx playwright test

# Everything: Rust workspace checks plus the UI e2e suite.
check-all: check test-site
