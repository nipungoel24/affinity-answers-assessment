PYTHON ?= python3

.PHONY: help install demo test test-live q1 q2 q3

help:
	@echo "make install       install Python dependencies"
	@echo "make demo          run Q1, Q2 (fast), Q3 live and the test suite (~30 s)"
	@echo "make test          offline test suite"
	@echo "make test-live     tests that hit mdcomputers.in, Rfam and GitHub"
	@echo "make q1 | q2 | q3  regenerate one question's committed output (q2: ~5 min)"

install:
	$(PYTHON) -m pip install -r requirements.txt

demo:
	PYTHON=$(PYTHON) bash scripts/run_demo.sh

test:
	$(PYTHON) -m pytest -q

test-live:
	LIVE=1 $(PYTHON) -m pytest -q -m live

q1:
	$(PYTHON) q1_python_scraper/scraper.py --search "external hard drive" --max-pages 3 \
		--output q1_python_scraper/sample_output.jsonl

q2:
	$(PYTHON) q2_sql_rfam/run_queries.py --full

q3:
	bash q3_unix_shell/parse_companies.sh "" q3_unix_shell/sample_output.tsv
