# Security

## Reporting a vulnerability

Please report security issues privately through
[GitHub security advisories](https://github.com/TsenkoTsenkov/ccenv/security/advisories/new),
not in a public issue.

## Threat model

ccenv runs on a single developer machine as that user. It protects against:

- **Secrets at rest in the clear.** Tokens are stored only in a sops+age
  encrypted file. ccenv never writes a decrypted copy; values are decrypted
  into memory or passed straight to the child process.
- **Cross-context leakage.** A stale `AWS_PROFILE`, `ANTHROPIC_BASE_URL` or
  `ANTHROPIC_API_KEY` exported in your shell is overridden for every
  environment, and subscription environments explicitly blank gateway
  variables and a global `apiKeyHelper`.
- **Global state changes.** ccenv never runs `gh auth switch`, never edits
  `~/.gitconfig` and never changes the default AWS profile. Identity is set
  per process.
- **Injection from configuration.** Names used in sops lookups are restricted
  to `[A-Za-z0-9_.-]`, and every value placed into a shell command
  (`apiKeyHelper`, `awsAuthRefresh`, `ccenv shell` output) is quoted with
  `shlex.quote`.
- **Unexpected credential prompts.** `ccenv run` reports expired credentials
  but never opens a browser or refreshes anything unless you pass `--login`.

It does **not** protect against:

- **Code running as your user.** It can read your age key, your gh keyring
  and your AWS SSO cache directly, exactly as ccenv can.
- **Processes inside a session.** Tokens exported into a session (`GH_TOKEN`,
  `secret_env`) are visible to every tool and subprocess that session runs,
  including the model's shell commands. Use fine-grained, narrowly scoped
  tokens per environment.
- **Pasted credentials.** `aws-handoff` output pasted into a cloud session is
  stored in that session's transcript. The command refuses to hand over
  long-lived keys and defaults to a one-hour token.

## Hardening checklist

- `chmod 600 ~/.config/sops/age/keys.txt`, and back the key up somewhere
  safe. Without it the secrets file cannot be decrypted.
- Keep `~/.config/ccenv` out of public repositories. The manifest is not
  secret, but it names your clients, accounts and directories.
- Store secrets with `ccenv secret <env> <key> --set` and no value argument.
  It prompts, or reads stdin, so the value stays out of shell history and `ps`.
- Prefer SSO profiles over static IAM keys, and short `--duration` values for
  `aws-handoff`.
