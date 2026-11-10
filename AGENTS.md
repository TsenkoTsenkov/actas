# AGENTS.md

Instructions for AI agents (Claude Code, Codex, Cursor and others) that read
this repository or run inside a session that ccenv launched.

## What ccenv is

ccenv is a single Python script (`bin/ccenv`) that launches Claude Code with
the right per-project configuration: model provider, AWS profile, GitHub
account and git identity, and env vars. The user's configuration lives
outside this repo in `~/.config/ccenv/` (or `$CCENV_ROOT`):

| File | Contents | Sensitive? |
|---|---|---|
| `environments.yaml` | Environment names, directory matches, profile and account names | Private. It names the user's clients and accounts. |
| `secrets.enc.yaml` | sops+age encrypted tokens | Encrypted, but never decrypt it into output |
| `.sops.yaml` | age public keys allowed to decrypt | Public keys only |

## If you are running inside a ccenv session

- `$CCENV` holds the active environment name. `ccenv shell` (without `eval`)
  prints its non-secret variables. Note that it also prints `GH_TOKEN` when
  the environment has a GitHub account, so do not run it into a transcript.
  Prefer `ccenv list`.
- AWS: `AWS_PROFILE` is already set. If an AWS call fails with an expired
  token, **tell the user to run `ccenv login <env>`**. Do not run
  `aws sso login` or `ccenv login` yourself: it opens a browser and needs the
  user anyway.
- GitHub and git: `GH_TOKEN` and the commit identity are set per process. Do
  not change `~/.gitconfig` or run `gh auth switch`. That would affect the
  user's other sessions.

## Safe to run

| Command | Why it's safe |
|---|---|
| `ccenv help`, `ccenv <cmd> --help` | Static text |
| `ccenv list` | Names only. No network, no secrets. |
| `ccenv list --check` | Probes AWS and secret availability. Prints status, never values. |
| `ccenv doctor` | Tool and key presence |
| `ccenv --cwd <dir> list` / `ccenv --cwd <dir> run --help` | Resolution checks |

## Ask the user first

| Command | Why |
|---|---|
| `ccenv secret <env> <key>` | Prints a decrypted secret to stdout, which ends up in your transcript. |
| `ccenv shell` | Output includes tokens (`GH_TOKEN`, `secret_env` values). |
| `ccenv aws-handoff` | Mints live AWS credentials. Only for handing to a cloud session, with consent. |
| `ccenv login`, `ccenv run --login` | Opens a browser for SSO. |
| `ccenv secret ... --set` | Changes the encrypted store. Never pass the value as an argument: pipe it on stdin. |
| `ccenv sync` | Writes `.claude/settings*.json` into project directories. |
| `ccenv run` | Replaces the current process with a new Claude Code session. |

Never print, log, commit or paste the output of `ccenv secret`, `ccenv shell`
or `ccenv aws-handoff`. Never decrypt `secrets.enc.yaml` with sops directly.

## Helping a user set up ccenv

1. Check prerequisites with `ccenv doctor`. Only Python 3.9+ and `claude` are
   required. sops+age, the AWS CLI and `gh` are needed only for the features
   that use them.
2. Run `ccenv init`. It never overwrites existing files.
3. Edit `~/.config/ccenv/environments.yaml`. Use
   [`examples/environments.yaml`](examples/environments.yaml) for reference.
   Ask the user for the real directory paths, AWS profile names
   (`aws configure list-profiles`) and gh accounts (`gh auth status`). Never
   guess them.
4. For secrets, tell the user to run `ccenv secret <env> <key> --set`
   themselves, so the value never passes through you.
5. Verify with `ccenv list --check` and `ccenv --cwd <project> list`.

## Working on this repository

- Keep it to one file and the standard library. A new runtime dependency
  needs a strong reason. Optional external tools (sops, aws, gh, yq) are
  called as subprocesses and only when a feature needs them.
- Python 3.9 compatible: no `match` statements, and no nested same-type quotes
  inside f-strings.
- Never write a secret to disk in the clear. Generated files go through
  `write_private()`, and anything interpolated into a shell command goes
  through `shlex.quote`.
- Names that reach sops lookups go through `check_name()`.
- Usage errors must print the full help (the `Parser` class). Add new options
  with a `help=` string so they appear in the menu.
- Run `python3 -m unittest discover -s tests` before committing, and add a
  test for new behaviour.
- Examples and tests use placeholder names only (`client-a`, `example.com`,
  `Ada Example`). Never use real organizations, accounts or paths.
