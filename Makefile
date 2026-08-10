SNAP_NAME := visiontak-client
PI ?= ubuntu@raspberrypi.local
SNAP_TAG ?= clean

.PHONY: help venv test lint snap install-pi run-local clean splash \
        vm-setup vm-run vm-deploy vm-ssh vm-snapshot vm-restore vm-reset

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

venv: ## Create a dev virtualenv
	python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

test: ## Run the unit tests (no GTK/CEC hardware needed)
	.venv/bin/pytest -q

lint: ## Lint
	.venv/bin/ruff check src tests

snap: ## Build the arm64 snap on a remote arm64 host (needs BUILD_HOST=user@ip)
	@test -n "$(BUILD_HOST)" || { echo "set BUILD_HOST=user@ip — see docs/arm64-build.md"; exit 1; }
	sh vm/remote-build.sh $(BUILD_HOST)

snap-amd64: ## Build the snap for amd64 (the local test VM)
	snapcraft pack --build-for=amd64 --use-lxd

install-pi: snap ## Copy and install the snap on a Pi running Ubuntu Core
	scp $(SNAP_NAME)_*_arm64.snap $(PI):/tmp/
	ssh $(PI) 'sudo snap install --dangerous /tmp/$(SNAP_NAME)_*_arm64.snap'
	ssh $(PI) 'sudo snap connect $(SNAP_NAME):hdmi-cec' || \
		echo "connect hdmi-cec manually once the gadget provides the slot"

# Invoked through `sh` so a Windows checkout without the exec bit still works.
splash: ## Generate the gadget boot splash (800x400) from assets/visiontak-logo.png
	@command -v convert >/dev/null || { echo "needs ImageMagick: apt install imagemagick"; exit 1; }
	@test -f assets/visiontak-logo.png || { echo "missing assets/visiontak-logo.png"; exit 1; }
	@mkdir -p build
	convert assets/visiontak-logo.png -resize 400x400 -background black \
		-gravity center -extent 800x400 build/vendor-logo.png
	@echo "build/vendor-logo.png -> copy to <pi-gadget>/splash/vendor-logo.png"

vm-setup: ## Provision the local Ubuntu Core test VM host (run as root, Ubuntu 24.04)
	sh vm/setup-host.sh

vm-run: ## Boot the local Ubuntu Core 24 VM
	sh vm/run.sh

vm-deploy: ## Build the snap and install it in the running VM (needs VM_USER=)
	sh vm/deploy.sh

vm-ssh: ## Shell into the running VM (needs VM_USER=)
	sh vm/ssh.sh

vm-snapshot: ## Save the VM's guest state (SNAP_TAG=clean)
	sh vm/snapshot.sh save $(SNAP_TAG)

vm-restore: ## Restore a saved guest state (SNAP_TAG=clean)
	sh vm/snapshot.sh restore $(SNAP_TAG)

vm-reset: ## Discard the VM's guest state (back to console-conf)
	sh vm/run.sh --reset-only

run-local: ## Run against a nested Ubuntu Frame on a dev desktop
	WAYLAND_DISPLAY=wayland-99 VISIONTAK_DATA_DIR=$(PWD)/.local-data \
		.venv/bin/python -m visiontak_client --verbose

clean:
	rm -rf .venv build dist *.snap parts prime stage .local-data
