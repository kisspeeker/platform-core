.PHONY: tf tf-init tf-plan tf-apply tf-state-backup tf-state-snapshots tf-state-restore \
	bootstrap bootstrap-pull artefacts artefacts-show d2

ARGS ?=
TASK ?=
# Обязательно: make … CUSTOMER=kisspeeker
CUSTOMER ?=
CUSTOMER_DIR := customers/$(CUSTOMER)
CUSTOMER_YAML := $(CUSTOMER_DIR)/infra.yaml
CUSTOMER_SECRETS := $(CUSTOMER_DIR)/secrets.sops.env
NETBIRD_SECRETS := $(CUSTOMER_DIR)/netbird.sops.yaml
NETBIRD_EXPORT := $(CUSTOMER_DIR)/dns/netbird/export-keys
DNS_DIR := $(CUSTOMER_DIR)/dns
SNAPSHOT ?= latest
REPO ?= PRIMARY
UV_RUN_FLAGS ?= --locked

# Docs: https://getsops.io/docs/usage/advanced/#passing-secrets-to-other-processes
SOPS := mise exec -- sops exec-env $(CUSTOMER_SECRETS)
PYINFRA = CUSTOMER_YAML="$(CUSTOMER_YAML)" uv run $(UV_RUN_FLAGS) pyinfra --chdir pyinfra inventory.py
RESTIC_ENV := export RESTIC_REPOSITORY="$$RESTIC_$(REPO)_REPOSITORY" \
	AWS_ACCESS_KEY_ID="$$RESTIC_$(REPO)_ACCESS_KEY_ID" \
	AWS_SECRET_ACCESS_KEY="$$RESTIC_$(REPO)_SECRET_ACCESS_KEY" \
	AWS_DEFAULT_REGION="$${RESTIC_$(REPO)_REGION:-auto}";
TF_ENV := export TF_VAR_tofu_state_passphrase="$$TOFU_STATE_PASSPHRASE" \
	TF_VAR_cloudflare_api_token="$$CF_ADMIN_TOKEN" \
	TF_VAR_netbird_pat_token="$$NETBIRD_PAT_TOKEN";
TOFU := $(TF_ENV) mise exec -- tofu -chdir=$(DNS_DIR)/tofu
TF_RESTIC_ENV := $(RESTIC_ENV) export RESTIC_PASSWORD="$$RESTIC_$(REPO)_PASSWORD";

define NEED_CUSTOMER
	@case "$(CUSTOMER)" in ''|*[!a-zA-Z0-9_-]*) \
		echo "error: CUSTOMER must match [a-zA-Z0-9_-]+ (got: '$(CUSTOMER)')" >&2; exit 1;; esac
	@test -f "$(CUSTOMER_YAML)" || { echo "error: not found: $(CUSTOMER_YAML) (symlink customers/$(CUSTOMER)?)" >&2; exit 1; }
	@test -f "$(CUSTOMER_SECRETS)" || { echo "error: not found: $(CUSTOMER_SECRETS)" >&2; exit 1; }
endef

tf:
	$(NEED_CUSTOMER)
	@$(SOPS) '$(TOFU) $(ARGS)'

tf-init:
	$(NEED_CUSTOMER)
	@$(SOPS) '$(TOFU) init $(ARGS)'

tf-plan:
	$(NEED_CUSTOMER)
	@$(SOPS) '$(TOFU) plan $(ARGS)'

tf-apply:
	$(NEED_CUSTOMER)
	@$(SOPS) '$(TOFU) apply $(ARGS)'
	@if [ -e "$(NETBIRD_EXPORT)" ]; then \
		test -x "$(NETBIRD_EXPORT)" || { echo "error: not executable: $(NETBIRD_EXPORT)" >&2; exit 1; }; \
		$(SOPS) "'./$(NETBIRD_EXPORT)'"; \
	fi
	@$(MAKE) --no-print-directory tf-state-backup CUSTOMER=$(CUSTOMER) REPO=PRIMARY
	@$(MAKE) --no-print-directory tf-state-backup CUSTOMER=$(CUSTOMER) REPO=OFFSITE

tf-state-backup:
	$(NEED_CUSTOMER)
	@$(SOPS) '$(TF_RESTIC_ENV) if [ -z "$$RESTIC_REPOSITORY" ]; then echo "skip state backup ($(REPO))"; else mise exec -- sh -c "cd $(DNS_DIR) && exec restic backup tofu/state --host customers/$(CUSTOMER)/dns --tag tofu-state"; fi'

tf-state-snapshots:
	$(NEED_CUSTOMER)
	@$(SOPS) '$(TF_RESTIC_ENV) mise exec -- restic snapshots --host customers/$(CUSTOMER)/dns --tag tofu-state $(ARGS)'

tf-state-restore:
	$(NEED_CUSTOMER)
	@test -n "$(SNAPSHOT)" || { echo "error: set SNAPSHOT=<id>" >&2; exit 1; }
	@$(SOPS) '$(TF_RESTIC_ENV) mise exec -- restic restore "$(SNAPSHOT)" --host customers/$(CUSTOMER)/dns --tag tofu-state --target $(DNS_DIR) $(ARGS)'

bootstrap-pull: UV_RUN_FLAGS = --upgrade-package edge-infra
bootstrap-pull: BOOTSTRAP_ENV = BOOTSTRAP_PULL_POLICY=always
bootstrap bootstrap-pull:
	$(NEED_CUSTOMER)
	@test -f "$(NETBIRD_SECRETS)" || { echo "error: not found: $(NETBIRD_SECRETS)" >&2; exit 1; }
	@$(SOPS) 'mise exec -- sops exec-file --no-fifo $(NETBIRD_SECRETS) '\''NETBIRD_CONFIG_FILE={} $(if $(BOOTSTRAP_ENV),$(BOOTSTRAP_ENV) )$(PYINFRA) $(if $(TASK),tasks/$(TASK).py,bootstrap.py) --limit customer $(ARGS)'\'''

artefacts:
	$(NEED_CUSTOMER)
	@$(SOPS) '$(PYINFRA) artefacts.py -y $(ARGS)'

artefacts-show:
	$(NEED_CUSTOMER)
	@mise exec -- sops decrypt $(CUSTOMER_DIR)/artefacts.sops.json | less

d2:
	$(NEED_CUSTOMER)
	@$(SOPS) 'CUSTOMER_YAML="$(CUSTOMER_YAML)" mise exec -- uv run --locked --directory pyinfra python topology.py'
