# Rust Gen Timeweb Node

This module creates a dedicated Linux worker for the native Rust renderer. It does not use the
legacy Windows custom image from `.env.iac` and it does not deploy application code from Terraform.
Cloud-init only prepares rootless Podman, the `rustgen` service user and an isolated job directory.

## State and inputs

Copy [`env.rust-gen.iac.example`](./env.rust-gen.iac.example) to the repository root as
`.env.rust-gen.iac`, then fill in a Linux `TWC_LINUX_OS_ID` discovered from Timeweb. At the
current account inventory, Debian 12 is `95`; re-check it before a regional rollout. Copy only
`TWC_TOKEN` from the old `.env.iac`; its `TWC_IMAGE_ID` is a Windows image and is invalid here.

Before `apply`, configure an encrypted remote Terraform backend in a local `backend.tf`. Do not
keep production state or credentials in this repository. The module intentionally refuses public
`0.0.0.0/0` manager and SSH ingress.

```bash
infra/timeweb/rust-gen-node/terraform.sh init
infra/timeweb/rust-gen-node/terraform.sh validate
infra/timeweb/rust-gen-node/terraform.sh plan
# apply is an explicit production change after reviewing the plan
infra/timeweb/rust-gen-node/terraform.sh apply
```

After apply, use the immutable-image workflow in `ae-native-renderer-v2` to deploy the manager.
The manager binds to loopback; expose it through a private network or a separately configured
authenticated reverse proxy before setting `RUST_GEN_MANAGER_URL` in the orchestrator.
