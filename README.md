# ccenv

**Per-project environments for [Claude Code](https://docs.claude.com/en/docs/claude-code).**
One manifest describes every context you work in: which model provider, which
AWS profile, which GitHub account, which env vars. `ccenv run` launches Claude
Code in the right one, resolved from the directory you are in.

Think `~/.aws/config`, but for the whole set of things a coding session needs,
so you can run a personal project, a client project and an open-source project
side by side without one leaking into the other.

```console
$ cd ~/code/client-a/api
$ ccenv run
ccenv: expired AWS credentials (not refreshing - log in only if you need them):
         client-a                   aws:client-a-sso     ccenv login client-a <- this session
ccenv: client-a · gateway · aws:client-a-sso · gh:ada-client-a
```

- **Single file, standard library only.** One Python 3.9+ script, no packages to install.
- **No secrets on disk in the clear.** Tokens live in a [sops](https://github.com/getsops/sops)
  + [age](https://github.com/FiloSottile/age) encrypted file and are fetched at request time.
- **Nothing global is touched.** AWS profile, GitHub token and git identity are
  set per process, so parallel sessions can be different people in different accounts.
- **Never blocks you.** Expired credentials produce a warning, not a login prompt.
  You refresh only what you need, when you need it.

---

## Contents

- [Why](#why)
- [Install](#install)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Manifest reference](#manifest-reference)
- [How a session is configured](#how-a-session-is-configured)
- [Secrets](#secrets)
- [AWS in cloud sessions](#aws-in-cloud-sessions)
- [Security model](#security-model)
- [For AI agents](#for-ai-agents)
- [Development](#development)

## Why

Four things vary per project, and each has its own way of switching:

| What | Can different sessions use different values at once? |
|---|---|
| AWS SSO | Yes. Tokens are cached per portal and all stay valid together. |
| Model provider (Anthropic API vs an LLM gateway) | Yes. They are plain env vars, set per process. |
| GitHub account | Yes. `gh auth token --user X` reads any stored token without switching the active account. |
| claude.ai login | **No.** There is one OAuth slot per machine. |

So parallel sessions in different contexts were always possible. What was
missing was a way to apply the right combination every time, without
remembering `export` lines or leaving a stale `AWS_PROFILE` in your shell that
quietly points a session at the wrong account. That is all ccenv does.

## Install

Requirements:

| Tool | Needed for | Required? |
|---|---|---|
| Python 3.9+ | everything | yes |
| [Claude Code](https://docs.claude.com/en/docs/claude-code) | `ccenv run` | yes |
| `sops` + `age` | encrypted secrets (gateway tokens, extra API tokens) | only if you use secrets |
| AWS CLI v2 | `aws:` blocks, `login`, `aws-handoff` | only if you use AWS |
| GitHub CLI `gh` | `github.account` | only if you switch GitHub accounts |
| PyYAML or `yq` | a YAML manifest | no. Without them, write the manifest as JSON |

```bash
git clone https://github.com/TsenkoTsenkov/ccenv ~/.local/share/ccenv
ln -s ~/.local/share/ccenv/bin/ccenv ~/.local/bin/ccenv   # any directory on your PATH

# optional, for secrets
brew install sops age            # or your package manager
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt && chmod 600 ~/.config/sops/age/keys.txt

ccenv init      # creates ~/.config/ccenv with a starter manifest (never overwrites)
ccenv doctor    # checks tools, keys and leaking shell exports
```

Your configuration lives in `~/.config/ccenv` (override with `CCENV_ROOT`).
It is separate from this repository: keep it private, or in a private repo of
its own. Never commit it to a public one.

Optional shell shortcuts (`cc` is taken by the C compiler):

```zsh
ccx() { ccenv run "$@"; }
ccl() { ccenv list --check; }
```

## Quick start

1. Edit `~/.config/ccenv/environments.yaml`:

   ```yaml
   version: 1
   defaults:
     provider: subscription

   environments:
     personal:
       match: [~/code/personal]
       github: { account: ada-personal, name: Ada Example, email: ada@example.com }

     client-a:
       match: [~/code/client-a]
       provider: gateway
       gateway: { base_url: https://llm-gateway.client-a.example, secret: token }
       aws: { profile: client-a-sso, region: eu-west-1 }
       github: { account: ada-client-a, name: Ada Example, email: ada@client-a.example }
   ```

2. Store the gateway token. You are prompted, so it never lands in shell history:

   ```bash
   ccenv secret client-a token --set
   ```

3. Check, then launch:

   ```bash
   ccenv list --check
   cd ~/code/client-a && ccenv run
   ```

[`examples/environments.yaml`](examples/environments.yaml) shows every option
with comments.

## Commands

Run `ccenv help <command>` for details. Any wrong command or option prints the
full menu.

| Command | What it does |
|---|---|
| `ccenv init` | Create `~/.config/ccenv` with a starter manifest, plus a `.sops.yaml` if an age key exists. Never overwrites. |
| `ccenv run [env] [--login] [--no-check] [-- claude args]` | Launch Claude Code in the environment matching the cwd, or a named one. Warns about expired AWS credentials across all environments, but only logs in with `--login`. |
| `ccenv list [--check]` | Show environments. `--check` also probes AWS sessions and secrets. |
| `ccenv shell [env]` | Print `export` lines: `eval "$(ccenv shell)"` configures a plain shell the same way. |
| `ccenv login [env] [--all]` | Refresh AWS SSO for one environment, or once per portal your environments use. |
| `ccenv secret <env> <key> [--set]` | Read a secret, or store one (prompted, or piped on stdin). |
| `ccenv sync [env] [--shared]` | Write `.claude/settings.local.json` into matching project directories. |
| `ccenv aws-handoff [env]` | Copy short-lived AWS credentials to the clipboard for a cloud session. |
| `ccenv doctor` | Check tools, keys and gh accounts, and flag shell exports ccenv will override. |
| `ccenv help [command]` | Show help. |

`--cwd DIR` works with any command, before or after it, and resolves the
environment as if you were in `DIR`. Anything after `--` is passed to
`claude` unchanged: `ccenv run client-a -- --resume`.

## Manifest reference

`~/.config/ccenv/environments.yaml`. Keys under `defaults` apply to every
environment unless the environment sets its own value.

| Key | Meaning |
|---|---|
| `provider` | `subscription` (api.anthropic.com, the default) or `gateway` (custom `ANTHROPIC_BASE_URL`). |
| `match` | Directory paths or globs. The longest match wins, so a nested repo can override its parent. |
| `aws.profile` / `aws.region` | AWS profile and region for this environment, instead of one global `AWS_PROFILE`. |
| `github.account` | Which stored `gh` account's token to use. It is read without switching the active account. |
| `github.name` / `github.email` | Commit identity, applied per process via `GIT_CONFIG_*` env vars. |
| `gateway.base_url` | The gateway endpoint, for `provider: gateway`. |
| `gateway.secret` | Key in the secrets file that holds the gateway token (default `token`). |
| `gateway.secret_from` | Read that key from another environment's secrets, to share one token. |
| `env` | Plain, non-secret env vars for the session. |
| `secret_env` | Env var name → secret key, for tokens that must not be plaintext. |
| `claude_args` | Extra flags always passed to `claude`. |

Environment and secret names may contain letters, digits, `.`, `_` and `-`.

## How a session is configured

There are two layers, because Claude Code can be started in more than one way.

**`ccenv run`** builds the process environment and `exec`s
`claude --settings <generated file>`. Use it from a terminal.

**`ccenv sync`** writes `.claude/settings.local.json` into each matching project,
so the right provider and AWS profile also apply when Claude Code is started
from an IDE, the desktop app or a bare `claude`. A settings `env` value
overrides the same variable exported in your shell. Existing keys such as
permissions are preserved, and AWS keys the environment no longer sets are
removed. `--shared` writes the committed `settings.json` instead. The default
stays local, so nothing lands in a shared repo by accident.

**Gateway environments cannot be driven from claude.ai.** Remote Control and
cloud sessions require `api.anthropic.com`. Plugins, MCP servers and skills
still work. `ccenv list` shows this in the `remote=` column.

## Secrets

Secrets live in `~/.config/ccenv/secrets.enc.yaml`, encrypted with sops to the
age keys listed in `~/.config/ccenv/.sops.yaml`. ccenv never writes a
decrypted copy:

- **Gateway tokens** reach Claude Code through `apiKeyHelper`, which runs
  `ccenv secret <env> <key>` each time a token is needed.
- **`secret_env` values** are decrypted into the `claude` process environment only.

To share secrets with a second machine or a teammate, add their age public key
to `.sops.yaml` (comma-separated) and run
`sops updatekeys ~/.config/ccenv/secrets.enc.yaml`.

## AWS in cloud sessions

Cloud sessions run on remote infrastructure and cannot read your `~/.aws`.
`ccenv aws-handoff` mints short-lived credentials and copies a ready-to-paste
setup block to the clipboard (macOS `pbcopy`; elsewhere it prints, as with
`--stdout`). Long-lived IAM keys are traded for a session token first, so they
are never handed over. Whatever you paste is recorded in that session's
transcript, so keep the TTL short and the role narrow.

[`skills/aws-handoff`](skills/aws-handoff/SKILL.md) is a Claude Code skill
that wraps this. Link it into `~/.claude/skills/` to use `/aws-handoff`.

## Security model

See [SECURITY.md](SECURITY.md). In short:

- The manifest is not secret: it holds names and pointers only.
- Secrets are encrypted at rest with sops+age and decrypted only in memory.
- Generated settings files hold no secrets, are created mode `0600`, and
  every value interpolated into a shell command is quoted.
- Names used in sops lookups are validated, so a manifest entry cannot alter
  which secret is read.
- `ccenv run` never opens a browser or changes credentials unless you pass `--login`.
- Tokens exported into a session (`GH_TOKEN`, `secret_env`) are visible to
  every process that session starts. Give each environment the narrowest
  token that works.

## For AI agents

If you are an AI agent working in or with this repository, read
[AGENTS.md](AGENTS.md). It explains what ccenv is, how to inspect an
environment safely, and which commands must not be run without the user's
explicit consent.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests use only the standard library and a temporary config directory.
The sops round-trip test runs when `sops` and `age-keygen` are installed.
CI runs the suite on Python 3.9 and 3.13.

## License

[MIT](LICENSE)
