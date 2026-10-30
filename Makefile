# Convenience targets (macOS / Linux / Git Bash). On Windows PowerShell, run the python commands directly.
.PHONY: install bootstrap serve test lint gate docker pages

install:
	pip install -r requirements.txt

bootstrap:
	python -m sentinel.cli bootstrap

serve:
	python -m sentinel.cli serve

test:
	python -m pytest

lint:
	ruff check sentinel tests

gate:
	python -m sentinel.cli eval --agent 6 && python -m sentinel.cli gate

docker:
	docker build -t sentinel:latest .

pages:  ## build the static GitHub Pages demo into ./site (needs bootstrap first)
	python scripts/build_pages.py site
