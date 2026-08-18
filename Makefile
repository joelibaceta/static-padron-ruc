PY ?= python3
ROWS ?= 50000
PORT ?= 8000

.PHONY: help fake build-fake build check guard serve vendor clean distclean

help:
	@echo "make fake        genera un padron sintetico en .work/fake_padron.txt"
	@echo "make build-fake  construye dist/ a partir del padron sintetico"
	@echo "make build       construye dist/ bajando el padron real de SUNAT (pesado)"
	@echo "make check       consulta la fuente y dice si hace falta reconstruir"
	@echo "make guard       verifica el presupuesto de tamano de dist/"
	@echo "make vendor      copia sql.js-httpvfs a web/vendor (requiere npm)"
	@echo "make serve       sirve dist/ en http://localhost:$(PORT)"
	@echo "make clean       borra dist/ y .work/"

fake:
	$(PY) tools/make_fake_padron.py --rows $(ROWS)

build-fake: fake
	$(PY) build/run.py build --local-txt .work/fake_padron.txt
	$(MAKE) guard

build:
	$(PY) build/run.py build
	$(MAKE) guard

check:
	$(PY) build/run.py check

guard:
	$(PY) build/guard.py

vendor:
	npm install --no-save --no-audit --no-fund sql.js-httpvfs
	mkdir -p web/vendor/sql.js-httpvfs
	cp -r node_modules/sql.js-httpvfs/dist/. web/vendor/sql.js-httpvfs/

# Ojo: NO usar `python -m http.server`. No implementa Range y sql.js-httpvfs
# termina leyendo bytes equivocados -> "database disk image is malformed".
serve:
	$(PY) tools/serve.py --dir dist --port $(PORT)

clean:
	rm -rf dist .work

distclean: clean
	rm -rf node_modules web/vendor package-lock.json
