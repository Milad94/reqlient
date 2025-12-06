# Justfile for reqflow - A command runner for common tasks
# Install just: https://github.com/casey/just

# Default recipe - show available commands
default:
    @just --list

# Install dependencies using uv
install:
    uv sync

# Install dependencies including dev dependencies
install-dev:
    uv sync --dev

# Install all optional dependencies
install-all:
    uv sync --all-extras

# Run tests
test:
    uv run pytest

# Run tests with verbose output
test-verbose:
    uv run pytest -v

# Run tests with coverage
test-cov:
    uv run pytest --cov=reqflow --cov-report=html --cov-report=term

# Run specific test file
test-file FILE:
    uv run pytest tests/{{FILE}}

# Run specific test class or function
test-target TARGET:
    uv run pytest {{TARGET}}

# Run tests in parallel
test-parallel:
    uv run pytest -n auto

# Run only unit tests
test-unit:
    uv run pytest -m unit

# Run only integration tests
test-integration:
    uv run pytest -m integration

# Run tests excluding slow tests
test-fast:
    uv run pytest -m "not slow"

# Format code with ruff
format:
    uv run ruff format .

# Check formatting without making changes
format-check:
    uv run ruff format --check .

# Lint code with ruff
lint:
    uv run ruff check .

# Fix linting issues automatically
lint-fix:
    uv run ruff check --fix .

# Format and lint in one command
check: format-check lint
    @echo "✓ Formatting and linting checks passed"

# Fix formatting and linting issues
fix: format lint-fix
    @echo "✓ Code formatted and linting issues fixed"

# Type check (if mypy is added)
typecheck:
    uv run mypy reqflow

# Run all checks (format, lint, test)
ci: check test
    @echo "✓ All CI checks passed"

# Clean build artifacts
clean:
    rm -rf build/
    rm -rf dist/
    rm -rf *.egg-info
    rm -rf .pytest_cache
    rm -rf .ruff_cache
    rm -rf htmlcov/
    rm -rf .coverage
    find . -type d -name __pycache__ -exec rm -r {} +
    find . -type f -name "*.pyc" -delete

# Clean and reinstall
reinstall: clean install-dev
    @echo "✓ Cleaned and reinstalled"

# Build package
build:
    uv build

# Build source distribution
build-sdist:
    uv build --sdist

# Build wheel
build-wheel:
    uv build --wheel

# Publish package to PyPI
publish:
    uv publish

# Publish package to TestPyPI
publish-test:
    uv publish --publish-url https://test.pypi.org/legacy/

# Build and publish package
build-publish: build publish
    @echo "✓ Package built and published"

# Show project info
info:
    @echo "Project: reqflow"
    @echo "Python version:"
    @uv run python --version
    @echo "\nInstalled packages:"
    @uv pip list

# Show dependency tree
deps:
    uv tree

# Update dependencies
update:
    uv sync --upgrade

# Update all dependencies including dev
update-all:
    uv sync --upgrade --dev --all-extras

# Run a Python script with the environment
run SCRIPT:
    uv run python {{SCRIPT}}

# Start a Python REPL with the environment
repl:
    uv run python

# Show help
help:
    @echo "reqflow - Common tasks:"
    @echo ""
    @echo "  Development:"
    @echo "    just install          - Install dependencies"
    @echo "    just install-dev      - Install with dev dependencies"
    @echo "    just test             - Run tests"
    @echo "    just test-cov         - Run tests with coverage"
    @echo "    just format           - Format code"
    @echo "    just lint             - Lint code"
    @echo "    just fix              - Format and fix linting"
    @echo "    just check            - Check formatting and linting"
    @echo ""
    @echo "  Building:"
    @echo "    just build            - Build package"
    @echo "    just build-sdist      - Build source distribution"
    @echo "    just build-wheel      - Build wheel"
    @echo "    just publish          - Publish package to PyPI"
    @echo "    just publish-test     - Publish package to TestPyPI"
    @echo "    just build-publish    - Build and publish package"
    @echo "    just clean            - Clean build artifacts"
    @echo ""
    @echo "  Other:"
    @echo "    just info             - Show project info"
    @echo "    just deps             - Show dependency tree"
    @echo "    just update           - Update dependencies"

