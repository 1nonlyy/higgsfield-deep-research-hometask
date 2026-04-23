# Run from repo root (task.md one-command setup).
.PHONY: test install

install:
	cd eval-framework && $(MAKE) install

test:
	cd eval-framework && $(MAKE) test
