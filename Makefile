PLUGIN   := Domoticz-Synology-SNMP
VERSION  := $(shell sed -n 's/^__version__ = "\(.*\)"$$/\1/p' synoSNMP/__init__.py)
PYTHON   ?= python3
DIST     := dist
ARCHIVE  := $(DIST)/$(PLUGIN)-$(VERSION).tar.gz

# make deploy HOST=jan@rpi4office                       deploy over SSH
# make deploy                                           deploy on this machine
# make deploy HOST=jan@rpi4office VERIFY=192.168.1.20   ... and query the NAS afterwards
HOST   ?=
VERIFY ?=
DEPLOY_ARGS ?=
_DEPLOY = ./deploy.sh $(if $(HOST),--host $(HOST)) $(if $(VERIFY),--verify $(VERIFY)) $(DEPLOY_ARGS)

.PHONY: help test lint compat check package deploy deploy-dry rollback version clean

help:
	@echo "make test        run the unit and end-to-end tests"
	@echo "make lint        pyflakes + shellcheck (when installed)"
	@echo "make compat      check the code runs on Python 3.7+ (needs 'vermin')"
	@echo "make check       test + lint + compat"
	@echo "make package     build $(ARCHIVE)"
	@echo "make deploy      install on HOST (default: this machine), see deploy.sh --help"
	@echo "make deploy-dry  show what deploy would do"
	@echo "make rollback    restore the newest backup on HOST"
	@echo "make version     print the version"

version:
	@echo $(VERSION)

test:
	$(PYTHON) -m unittest discover -s tests

lint:
	@if $(PYTHON) -m flake8 --version >/dev/null 2>&1; then \
	    echo "flake8 (pyflakes checks)"; \
	    $(PYTHON) -m flake8 --select=F --builtins=Parameters,Devices,Images plugin.py synoSNMP tests tools; \
	elif $(PYTHON) -m pyflakes --version >/dev/null 2>&1; then \
	    echo "pyflakes"; \
	    PYFLAKES_BUILTINS=Parameters,Devices,Images $(PYTHON) -m pyflakes plugin.py synoSNMP tools; \
	else echo "flake8 not installed (pip install flake8) - skipped"; fi
	@if command -v shellcheck >/dev/null 2>&1; then echo "shellcheck"; shellcheck deploy.sh tools/*.sh; \
	else echo "shellcheck not installed - skipped"; fi

compat:
	@if command -v vermin >/dev/null 2>&1; then vermin --target=3.7- --no-tips --violations -q plugin.py synoSNMP; \
	else echo "vermin not installed (pip install vermin) - skipped"; fi

check: test lint compat

package:
	@mkdir -p $(DIST)
	tar --exclude=./$(DIST) --exclude=./.git --exclude='__pycache__' --exclude='*.pyc' --exclude=./.pytest_cache \
	    --transform 's,^\.,$(PLUGIN),' -czf $(ARCHIVE) .
	@echo "built $(ARCHIVE)"

deploy:
	$(_DEPLOY)

deploy-dry:
	$(_DEPLOY) --dry-run

rollback:
	./deploy.sh $(if $(HOST),--host $(HOST)) --rollback $(DEPLOY_ARGS)

clean:
	rm -rf $(DIST) .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
