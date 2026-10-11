PREFIX ?= /usr/local
PYTHON ?= python3
TOOLS = setwallpaper vtm-terminal vtm-lua vtmpanel screenshow kmsgrab vtm-clipboard vtm-clipboard-server vtm-tools-exec

.PHONY: all check install
all:
	@echo "Python scripts: no compilation required. Use make check / make install."
check:
	$(PYTHON) -m unittest discover -s tests -v
install:
	install -d "$(DESTDIR)$(PREFIX)/bin" "$(DESTDIR)$(PREFIX)/share/vtm-tools/vtm_tools" "$(DESTDIR)$(PREFIX)/share/doc/vtm-tools"
	for tool in $(TOOLS); do install -m755 "bin/$$tool" "$(DESTDIR)$(PREFIX)/bin/$$tool"; done
	install -m644 vtm_tools/*.py "$(DESTDIR)$(PREFIX)/share/vtm-tools/vtm_tools/"
	install -m644 README.md LICENSE config.example.toml "$(DESTDIR)$(PREFIX)/share/doc/vtm-tools/"
