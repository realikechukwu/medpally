.PHONY: format ci

# Match the fast feedback gates in GitHub Actions before pushing a branch.
format:
	uv run ruff format .

ci:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy engine/
	uv run python manage.py makemigrations --check --dry-run
	uv run pytest -m "not network" --cov --cov-report=term-missing
