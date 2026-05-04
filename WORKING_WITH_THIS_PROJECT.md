# Working With This Project

Operational note for Codex sessions in this repo.

As of 2026-05-03, assume we work against the deployed servers, not a local
`.env`-driven runtime. Do not rely on local SSH aliases.

## Timeweb / IAC

- `.env.iac` contains `TWC_TOKEN` for the Timeweb Cloud API and Terraform/IAC
  work. Use the token from that file when discovering project infrastructure.
- Do not print or commit token values, passwords, API keys, or session strings.
- Prefer explicit API/WinRM failures over hidden recovery or implicit fallback.

## Blast Servers

Use the Timeweb project that contains the `blast` infrastructure. The known
Windows render nodes are:

- `blast-worker-node-0` at `85.239.48.31`
- `blast-render-node-dist` at `72.56.246.24`

When a task asks to pick a Windows render node and does not specify otherwise,
prefer the node whose public IPv4 starts with `72` (`72.56.246.24`).

## Windows Access

- Connect directly over WinRM; ignore local SSH aliases.
- Default Windows user is `Administrator`.
- Resolve the current Windows password from Timeweb server details using
  `TWC_TOKEN`; do not write the password into repo files or logs.
- After Effects lives on the Windows node and is driven remotely through WinRM,
  scheduled tasks, or the render-node API depending on the task.
