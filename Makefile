PY ?= python3
PORT ?= 8000

.PHONY: help build check guard serve clean

help:
	@echo "make build   build dist/ by downloading SUNAT's real padron (heavy)"
	@echo "make check   query the source and report whether a rebuild is needed"
	@echo "make guard   verify dist/'s size budget"
	@echo "make serve   serve dist/ at http://localhost:$(PORT)"
	@echo "make clean   remove dist/ and .work/"

build:
	$(PY) build/run.py build
	$(MAKE) guard

check:
	$(PY) build/run.py check

guard:
	$(PY) build/guard.py

serve:
	$(PY) -m http.server $(PORT) --directory dist

clean:
	rm -rf dist .work
