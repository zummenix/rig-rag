# List available recipes.
default:
    @just --list

# Serve the UI in watch mode (hot reload; proxies /api/* to :8080).
dev-site:
    cd site && trunk serve

# Run the API server, rebuilding on server changes.
dev-api:
    cargo watch -w Cargo.toml -w src -w shared -x 'run --bin rig-rag -- serve'

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

# Run the offline Python eval unit tests (stdlib unittest), runner + report.
eval-test:
    python3 -m unittest discover -s eval -t .

# Run the retrieval-eval runner; pass flags after `--`, e.g. `just eval-run --profiles single-project`.
eval-run *args:
    python3 -m eval.runner {{args}}

# Render an experiment's newest run to eval/reports/<experiment>.html.
# e.g. `just eval-report baseline`.
eval-report experiment:
    python3 -m eval.experiments.{{experiment}}.report

# Everything: Rust workspace checks, the UI e2e suite, and the eval tests.
check-all: check test-site eval-test
