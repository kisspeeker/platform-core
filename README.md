# Platform

Automation for setting up, securing, and running Linux servers and Docker workloads with `pyinfra`.

Deployments, firewall rules, monitoring, and backups all live in one place.

```sh
mise install && uv sync --upgrade
```

```sh
export CUSTOMER=<name>

# 1. symlink gitops (or git clone)
ln -sfn /path/to/${CUSTOMER}-gitops customers/$CUSTOMER

# 2. DNS
make tf-init CUSTOMER=$CUSTOMER
make tf-apply CUSTOMER=$CUSTOMER

# 3. bootstrap
make bootstrap CUSTOMER=$CUSTOMER ARGS='--dry'
make bootstrap CUSTOMER=$CUSTOMER

# 4. клиентские артефакты
make artefacts CUSTOMER=$CUSTOMER
```


```sh
sops customers/$CUSTOMER/secrets.sops.env
make d2 CUSTOMER=$CUSTOMER
make restic-status CUSTOMER=$CUSTOMER
make restore CUSTOMER=$CUSTOMER HOST=<name> SNAPSHOT=<id>
```
