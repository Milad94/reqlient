# Justfile for reqflow - A resilient HTTP client library
# Install just: https://github.com/casey/just

# ============================================================================
# DEFAULT
# ============================================================================

# Show available commands
default:
    @just --list --unsorted

# ============================================================================
# INSTALLATION
# ============================================================================

# Install dependencies
install:
    uv sync

# Install with dev dependencies
install-dev:
    uv sync --dev

# Install all optional dependencies
install-all:
    uv sync --all-extras

# Clean and reinstall
reinstall: clean install-dev
    @echo "✓ Cleaned and reinstalled"

# Update dependencies
update:
    uv sync --upgrade

# Update all dependencies including dev
update-all:
    uv sync --upgrade --dev --all-extras

# ============================================================================
# TESTING
# ============================================================================

# Run all tests
test:
    uv run pytest

# Run tests with verbose output
test-verbose:
    uv run pytest -v

# Run tests with coverage report
test-cov:
    uv run pytest --cov=reqflow --cov-report=html --cov-report=term

# Run tests in parallel (faster)
test-parallel:
    uv run pytest -n auto

# Run specific test file (e.g., just test-file sync/test_rest_client.py)
test-file FILE:
    uv run pytest tests/{{FILE}}

# Run specific test target (e.g., just test-target tests/sync/test_rest_client.py::TestRestClientGet)
test-target TARGET:
    uv run pytest {{TARGET}}

# Run only unit tests
test-unit:
    uv run pytest -m unit

# Run only integration tests
test-integration:
    uv run pytest -m integration

# Run tests excluding slow tests
test-fast:
    uv run pytest -m "not slow"

# ============================================================================
# CODE QUALITY
# ============================================================================

# Format code with ruff
format:
    uv run ruff format .

# Check code formatting (without fixing)
format-check:
    uv run ruff format --check .

# Lint code with ruff
lint:
    uv run ruff check .

# Lint and auto-fix issues
lint-fix:
    uv run ruff check --fix .

# Static type check
typecheck:
    uv run mypy

# Format and fix all issues
fix: format lint-fix
    @echo "✓ Code formatted and linted"

# Check formatting, linting, and types (no fixes)
check: format-check lint typecheck
    @echo "✓ All checks passed"

# ============================================================================
# PRE-COMMIT
# ============================================================================

# Install pre-commit hooks
pre-commit-install:
    uv run pre-commit install
    @echo "✓ Pre-commit hooks installed"

# Run pre-commit on all files
pre-commit:
    uv run pre-commit run --all-files

# Update pre-commit hooks to latest versions
pre-commit-update:
    uv run pre-commit autoupdate
    @echo "✓ Pre-commit hooks updated"

# ============================================================================
# CI / FULL CHECKS
# ============================================================================

# Run all CI checks (format, lint, test)
ci: check test
    @echo "✓ All CI checks passed"

# Run full CI with coverage
ci-full: check test-cov
    @echo "✓ All CI checks passed with coverage"

# ============================================================================
# BUILDING & PUBLISHING
# ============================================================================

# Build package (sdist and wheel)
build:
    uv build

# Build source distribution only
build-sdist:
    uv build --sdist

# Build wheel only
build-wheel:
    uv build --wheel

# Publish to PyPI
publish:
    uv publish

# Publish to TestPyPI
publish-test:
    uv publish --publish-url https://test.pypi.org/legacy/

# Build and publish to PyPI
release: build publish
    @echo "✓ Package built and published"

# ============================================================================
# CLEANUP
# ============================================================================

# Clean all build artifacts and caches
clean:
    rm -rf build/
    rm -rf dist/
    rm -rf *.egg-info
    rm -rf .pytest_cache
    rm -rf .ruff_cache
    rm -rf .mypy_cache
    rm -rf htmlcov/
    rm -rf .coverage
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    @echo "✓ Cleaned build artifacts"

# ============================================================================
# UTILITIES
# ============================================================================

# Show project info
info:
    @echo "Project: reqflow"
    @echo "Python version:"
    @uv run python --version
    @echo ""
    @echo "Installed packages:"
    @uv pip list

# Show dependency tree
deps:
    uv tree

# Run a Python script
run SCRIPT:
    uv run python {{SCRIPT}}

# Start Python REPL
repl:
    uv run python

# ============================================================================
# HELP
# ============================================================================

# Show detailed help
help:
    @echo "reqflow - A resilient HTTP client library"
    @echo ""
    @echo "Installation:"
    @echo "  just install           Install dependencies"
    @echo "  just install-dev       Install with dev dependencies"
    @echo "  just install-all       Install all optional dependencies"
    @echo "  just update            Update dependencies"
    @echo ""
    @echo "Testing:"
    @echo "  just test              Run all tests"
    @echo "  just test-cov          Run tests with coverage"
    @echo "  just test-parallel     Run tests in parallel"
    @echo "  just test-file FILE    Run specific test file"
    @echo ""
    @echo "Code Quality:"
    @echo "  just format            Format code with ruff"
    @echo "  just lint              Lint code with ruff"
    @echo "  just fix               Format and fix all issues"
    @echo "  just check             Check formatting and linting"
    @echo ""
    @echo "Pre-commit:"
    @echo "  just pre-commit-install  Install pre-commit hooks"
    @echo "  just pre-commit          Run pre-commit on all files"
    @echo "  just pre-commit-update   Update pre-commit hooks"
    @echo ""
    @echo "CI:"
    @echo "  just ci                Run all CI checks"
    @echo "  just ci-full           Run CI with coverage"
    @echo ""
    @echo "Building:"
    @echo "  just build             Build package"
    @echo "  just publish           Publish to PyPI"
    @echo "  just release           Build and publish"
    @echo "  just clean             Clean build artifacts"
    @echo ""
    @echo "Utilities:"
    @echo "  just info              Show project info"
    @echo "  just deps              Show dependency tree"
    @echo "  just repl              Start Python REPL"
