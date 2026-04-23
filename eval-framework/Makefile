# Intended to be run with cwd = eval-framework/ (e.g. repo `make test` cd's here first).
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

.PHONY: install test test-single dry-run diff

install:
	pip install -r $(ROOT)/requirements.txt

test:
	python -m pytest tests/ -v

test-single:
	python main.py run \
		--cases cases/happy_path_voyager.yaml \
		--agent .. \
		--output reports/single_run.json

dry-run:
	python main.py run \
		--cases cases/ --dry-run

diff:
	python main.py run \
		--cases cases/ \
		--output reports/run_new.json \
		--prev reports/run_old.json
