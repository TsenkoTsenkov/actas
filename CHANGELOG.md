# Changelog

All notable changes to ccenv. Version numbers follow
[Semantic Versioning](https://semver.org/).

## [2.0.0] - Unreleased (2026-11-11)

ccenv becomes a **credential context env**: it applies a profile's LLM
provider, cloud accounts, GitHub account, git identity and secrets to any
command or agent, not only to Claude Code. **v1 manifests keep working
unchanged** (see [Upgrading from v1](#upgrading-from-v1)).

### Added

- **Manifest version 2** (`version: 2`): `profiles`, reusable `roles`,
  composition with `use:`, `defaults` and a `fallback` profile. Unknown keys
  warn with a "did you mean" suggestion, and type errors name the exact path.
- **Levels and confirmation:** `level: readonly | standard | power` and
  `confirm: true` (a human types the profile name). Levels are the maximum
  across the `use:` chain, and under-declaring one is an error.
- **Permission sets:** roles that carry `permissions.deny` / `ask` lists.
  These lists, and `unset`, are combined across layers and can never be
  dropped by a later layer.
- **Modules:** `aws`, `gcp`, `azure`, `github`, `git`, `env`, `secret_env`.
  `null` on a module isolates it (an empty ccenv-owned config), and
  `"inherit"` passes the shell's values through. New `home:` keys give
  per-account `gh`, `gcloud` and `az` logins.
- **GitHub:** `github.auth: "gh:<login>[@host]"` or any secret ref, GitHub
  Enterprise hosts (`GH_ENTERPRISE_TOKEN`, `GH_HOST`), and `github.home` for an
  isolated `gh` login. A git credential helper is attached for HTTPS.
- **git:** SSH key, commit signing, per-URL credentials (GitLab, Bitbucket,
  Azure DevOps) and raw config, all set per process through `GIT_CONFIG_*`.
- **Claude Code providers:** `subscription`, `gateway`, `api-key`, `bedrock`
  and `vertex`, plus model pins. `claude.home` gives a separate login per
  subscription (`CLAUDE_CONFIG_DIR`).
- **Codex and Gemini CLI** (environment level): `ccenv run -t codex|gemini`,
  per-account homes, `ccenv login` for Codex, and Gemini's `--approval-mode`
  from the level.
- **Secret schemes:** `sops:`, `keychain:` (macOS), `op://`, `gh:` and `cmd:`.
  `ccenv secret set REF` stores a value (sops, keychain), and
  `ccenv secret --ref REF` reads one.
- **Commands:** `exec` (any command with a profile's identity), `status --why`
  (the `use:` chain and where each value comes from), `status --probe`,
  `--json` on read-only commands, `desktop` (with `--print-3p` for Claude
  Desktop's third-party inference setup and `--vscode`), `instructions`
  (agent rules, or a direnv function), and `migrate`. `shell` gains
  `--format fish|direnv|json|env` and `--no-secrets`. `login` gains
  `--module` and `--adc`; `login --all` groups profiles by `[sso-session]` or
  start URL. `doctor` checks the manifest, supports `--strict`, and `--fix`
  tightens ccenv's own directories to 0700. It also warns about profiles
  without `aws` when `~/.aws/config` has a `[default]` profile, and about
  `.env` files Gemini CLI would load.
- **Two-tier help:** `ccenv help` shows the eight everyday commands and one
  line of advanced ones; `ccenv help manifest` and `ccenv help agents` are
  short primers; `ccenv --help` lists every option.
- **`ccenv init`** writes a short version 2 starter and works without sops
  (it suggests `keychain:` or `op://` refs when there is no age key).
- `examples/minimal.yaml`, the manifest from the README quick start.
- **Agent guardrails:** agent detection, a session anchor written by
  `ccenv run`, an elevation gate for `run`/`exec` (`agents_may_use`, levels,
  `confirm`), a `ccenv-stop:` line on every refusal with exit codes 3 (needs
  the user) and 4 (credentials expired), human-only commands, an audit log at
  `~/.local/state/ccenv/audit.jsonl`, and built-in Claude Code permission
  rules per level. `permissions.builtin: false` opts a profile out.
- **SessionStart note:** a short context line tells Claude which profile it is
  in (written for `run` and `sync`).
- **Packaging:** `install.sh` (SHA-256 checked, never sudo, symlink-safe),
  `uv tool install` / `pipx install` from git, and a release workflow with a
  build-provenance attestation. In a uv or pipx install, helper strings
  call the installed `ccenv` command itself, so they survive upgrades.
- `contrib/direnvrc` and the `skills/ccenv` Claude Code skill.

### Changed

- **Scrubbing:** in a v2 manifest, `run`, `exec` and `shell` clear the
  variables owned by every module and tool the manifest references, and every
  variable some profile sets through `secret_env` (so one client's
  `NPM_TOKEN` does not reach another's session), unless the profile sets them
  (`run` names what it cleared). Use `<module>: inherit` or `inherit: [VAR]`
  to keep a variable. Plain `env:` names are not cleared for other profiles
  (use `unset:`), and session basics (`SSH_AUTH_SOCK`, `DISPLAY`, `EDITOR`,
  `LC_*`, `XDG_*`, …) never are.
- **The empty `_off` homes stay empty.** A login made inside one (for example
  `gh auth login` in a fail-closed shell) is moved aside with a warning before
  the next `run`/`exec`/`shell`/`login`/`sync` uses it, `status` warns, and
  `doctor` reports it.
- **Session records are also kept at a fixed place,** `~/.local/state/ccenv/sessions/`
  under your passwd home (from the user database; no variable moves it), so an
  agent inside `ccenv run` that drops `CCENV_SESSION` and moves
  `XDG_STATE_HOME` or `HOME` is still refused with `override`. That copy is
  searched first, so a record written into a moved `CCENV_STATE` does not
  beat it. Agent detection runs `/bin/ps` or `/usr/bin/ps` before any `ps` on `PATH`.
- `ccenv login --module github` with a `gh:<user>@<host>` account no longer
  "switches back" when that account was already active, and `doctor` lists
  each gh account once.
- **`GIT_CONFIG_*` written by `sync`, `shell --format env` and
  `desktop --vscode` carry `CCENV_GIT_BASE=0`,** so a `ccenv exec`/`run` from
  bare `claude`, Desktop or the IDE replaces those pairs (including the
  previous profile's credential helper) instead of keeping them.
- **`sync --clean`** now only removes what ccenv wrote in files that no
  profile syncs any more (listed as `stale`), and keeps a file whose removal
  would let `~/.claude/settings.json`'s `apiKeyHelper` or
  `env.ANTHROPIC_BASE_URL` apply there unless you add `--force`. A plain
  `sync` refreshes a file ccenv wrote earlier in a nested directory (v1 wrote
  those) instead of suggesting `--clean`. A profile that fails to load is
  skipped, and its files are kept (never treated as stale).
- **`ccenv login --module codex` with `login: api` needs `codex.home`:** Codex
  stores the key in plain text in its home, and without one that is the global
  `~/.codex`. `doctor` warns about such profiles.
- Errors about a malformed ref never repeat its value (it is often a pasted
  secret) and suggest `sops:<profile>/<field>`.
- A v1 key-name field (`secret_env` value, `gateway.secret`,
  `gateway.secret_from`) that looks like a pasted token is an error that does
  not repeat the value, so it never reaches warnings or `migrate` output. A
  token is a known prefix (`sk-`, `gh[pousr]_`, `github_pat_`, `glpat-`,
  `npm_`, `hf_`, `AIza`, `xox?-`, `xapp-`) followed by 16 or more letters and
  digits (or a body with separators that mixes digits and capitals), an
  Anthropic key (`sk-ant-api03-…`), a JWT (`eyJ…`), an `AKIA…`/`ASIA…` key, or
  any value you wrote that is longer than 64 characters. Key names such as
  `npm_token` or `ghs_deploy` load as in v1. It is a heuristic: a short or
  unusual token can still pass.
- **Gateway tokens are no longer exported in v2.** They reach Claude only
  through `apiKeyHelper`. Set `claude.export_token: true` to export them.
- **GitHub token auth fails closed in v2.** `GH_CONFIG_DIR` points at an
  empty directory, so a missing token makes `gh` report "not logged in"
  instead of using the keyring's active account. Use `github.home` if you
  need gh's own config and aliases.
- **Agent gate tightened:** a nested `ccenv run` started by an agent keeps the
  outer session's `agents_may_use` reach, so hops do not chain. An agent with
  no session (no record, no `CCENV`) may use only the readonly profile of its
  own working directory. When `ccenv run` started the agent (`CCENV_SESSION`)
  but its record is not found, for example after `XDG_STATE_HOME` or `HOME`
  changed, exec is refused with `reason=override`; without a process table,
  `$CCENV` is not trusted. `CCENV_ROOT` / `CCENV_STATE` equal to the default
  place no longer count as an override.
- **An explicit `--cwd` chooses the profile** for `status`, `exec` and `run`,
  also inside agent and direnv sessions (the gate still decides); the session
  profile is the default only when neither a name nor `--cwd` is given.
- **`mismatch` is false** when the directory matches only the `fallback:`
  profile (or nothing); agent rule 2 now says "matched to another profile".
- **`ccenv exec <p> -- aider`** (or another known agent CLI name) sets
  `CCENV_AGENT` for the tool, since macOS shows script-based agents as
  `python` or `node`.
- **`sync` reaches the repos inside a matched folder:** when a matched
  directory is not a git repo, sync also writes each git repo up to two levels
  below it that resolves to the same profile (Claude reads settings per repo).
  A v1-written file in a git worktree is kept current too.
- **`sync` gives back what it took over:** when a profile's `claude.settings`
  or `env` replaces a value you had set in `settings.local.json`, the ledger
  keeps your value and restores it when the profile stops setting the key or
  on `sync --clean`. For an env name that looks like a credential (`TOKEN`,
  `KEY`, `SECRET`, `PASS`, `CREDENTIAL`, `AUTH`) the ledger keeps only that it
  existed, and sync tells you its value was overwritten. Your value is kept
  even when it equals the profile's. Values ccenv wrote itself (`CCENV*`, or
  a file it synced before the ledger was lost) are never taken for yours; a v1 subscription file (empty `apiKeyHelper` and
  `ANTHROPIC_BASE_URL`) is adopted like other v1 files.
- **`gh` keeps working in your own direnv/shell:** `shell --no-secrets` (what
  `use ccenv` runs) no longer points `GH_CONFIG_DIR` at the empty `_off` home
  outside agent sessions, so `gh` uses its keyring there; `run`, `exec`,
  synced files and agent sessions still fail closed.
- `*.ghe.com` token auth sets `GH_HOST`, like GitHub Enterprise Server.
- `shell --format env` for a gateway profile notes on stderr that
  `ANTHROPIC_BASE_URL` comes without a credential (use `run`, `sync` or
  `desktop --print-3p`).
- `list`, `status`, the run badge and the SessionStart note report the
  provider a truthy `CLAUDE_CODE_USE_*` in `env` selects (for example
  `foundry`, with Remote Control off).
- `doctor` no longer lists a profile's own secret variables as cleared, offers
  `aws: inherit` only for AWS variables, says "after ccenv migrate" for v1
  files, and its `aws default` check also reads `~/.aws/credentials`.
- `run` in a nested profile's directory notes the repo root's synced file as
  information; it never suggests switching to the other profile.
- Helper strings no longer pin a conda env's python (treated like a venv).
- `list --check` probes each distinct AWS profile (and `aws.config_file`)
  once, six at a time, with the profile's own environment.
- One AWS check everywhere (`run`, `list --check`, `login`, `aws-handoff`):
  only expired credentials show as `expired`; a profile missing from the AWS
  config shows `missing`, other errors `unknown`. `aws.sso_session` also
  applies to `run --login` and `aws-handoff --login`.
- Without a YAML parser, `ccenv init` writes the starter as JSON and says how
  to get one, and `doctor` reports a manifest it cannot parse as a check
  instead of exiting. Debian and Ubuntu's Python `yq` now works as a parser.
  `install.sh` mentions it when neither PyYAML nor `yq` is installed, and
  explains that `latest` never installs a prerelease.
- `login --module github` with a `gh:` account switches gh's active account
  back to the previous one afterwards.
- `aws-handoff` picks `wl-copy` only in a Wayland session and `xclip` only
  with `DISPLAY` set, and no longer hangs while `xclip` serves the clipboard.
- Helper strings call `/usr/bin/python3` instead of the Xcode or Command Line
  Tools python behind it, so they survive an Xcode switch or removal.
- `help exec` and `help secret` show every call form; `help <unknown>` prints
  only the error. `status` abbreviates the matching pattern with `~`.
- Generated settings files are named by content hash (so concurrent runs never
  share one) and pruned after 7 days.
- Unchanged: the binary name, the config location (`~/.config/ccenv`) and
  the `ccenv secret ENV KEY` form that synced helpers call.

### Removed

- `sync --shared`. `sync` writes only the local, uncommitted
  `settings.local.json`. (A safer `--shared` is planned for 2.1.)

### Upgrading from v1

A v1 file (no `version`, or `version: 1`) is translated on load and runs on
the v2 engine. These are the behaviour changes you get **without editing the
file**:

- **Scrubbing, v1 scope only.** The `ANTHROPIC_*` credentials, URL and
  headers, `CLAUDE_CODE_USE_*`, `CLAUDE_CODE_OAUTH_TOKEN` and `GIT_CONFIG_*`
  pairs above the base are cleared. The parent shell's AWS profile, region and
  credential variables are cleared only for environments with an `aws:`
  block, and `GH_TOKEN`/`GITHUB_TOKEN` only for those with a GitHub account or
  `secret_env.GH_TOKEN`: an environment without an `aws:` block keeps your
  shell's `AWS_PROFILE`, as v1 did (`ccenv migrate` writes `aws: inherit`
  there to keep that; change it to `aws: null` to isolate). `CLAUDE_CONFIG_DIR`, `OPENAI_API_KEY`,
  `GOOGLE_*` and `ARM_*` are not touched.
- **Subscription mode unsets** `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`
  and `ANTHROPIC_API_KEY` instead of setting them to `""`. Settings also blank
  `CLAUDE_CODE_USE_MANTLE` and `CLAUDE_CODE_USE_ANTHROPIC_AWS`.
- **New variables in sessions:** `CCENV_LEVEL`, `CCENV_SESSION`,
  `CCENV_AGENT` and `CCENV_GIT_BASE`; in agent sessions also
  `GH_PROMPT_DISABLED=1`, `GIT_TERMINAL_PROMPT=0` and `AWS_PAGER=`.
- **Settings files** generated by `run` carry every non-secret variable of the
  session (for example `AWS_DEFAULT_REGION`, the git identity, `CCENV`), the
  provider blanks and a SessionStart hook.
- **GitHub:** `GITHUB_TOKEN` is set alongside `GH_TOKEN` from
  `secret_env.GH_TOKEN`. With both `github.account` and `secret_env.GH_TOKEN`,
  `GITHUB_TOKEN` is now the PAT. The git credential helper resolves the token
  when git asks for it, and is now also attached to a PAT from `secret_env`.
- **Helper strings** (`apiKeyHelper`, `awsAuthRefresh`, the git helper) use
  absolute, venv-safe paths and the `secret --ref` form. Synced files pick
  them up on the next `ccenv sync`. The old `ccenv secret ENV KEY` form still
  works.
- **A missing secret leaves the variable unset** rather than keeping the
  shell's value, which could belong to another identity.
- **sync** targets the profile that wins at each directory (longest match),
  writes at the git root (the main checkout for worktrees), skips a nested
  profile under another profile's repo and reports any leftover file there,
  and keeps a v1-written file in a nested directory or a worktree current. A
  matched folder that is not a repo also gets the file in each git repo up to
  two levels below it that resolves to the same profile. Synced files now
  carry every non-secret session variable: the git identity, a github.com
  credential helper that replaces your global one (`gh:<account>` through
  ccenv), `CCENV`/`CCENV_LEVEL` and the non-interactive variables, so bare
  `claude`, IDE and Desktop sessions in those repos commit and push as the
  profile's account.
  Writes are atomic with mode 0600. A ledger records what ccenv wrote, so
  removed keys are cleaned up, and `sync --clean` removes files no profile
  syncs any more. sync prints, but
  never applies, the `.gitignore` hint.
- **Validation:** `null` in `env` (and in `secret_env`) unsets the variable. Invalid env names and
  unknown `provider` values are errors. Boolean env values warn. A string
  `claude_args` is split like a shell command line.
- **Secrets:** exactly one trailing newline is stripped from a secret value.
- **`run`** checks only the target's AWS profile, not every profile in the
  manifest.
- **`aws-handoff`** no longer logs in implicitly (expired credentials exit 4
  unless you pass `--login`). Without a clipboard tool it prints only with
  `--stdout`. It refuses inside agent sessions.
- **Agents:** inside an agent session, `run`/`exec` of another profile is
  refused (exit 3, `ccenv-stop:`) unless the gate allows it. `login`, `sync`,
  `init`, `secret --set`, `aws-handoff`, `migrate --write` and `shell` with
  secrets are human-only.
- **CLI:** `--` after a command other than `run` or `exec` is a usage error
  (exit 2). `doctor` exits 1 when the manifest has errors.
- GitHub logins containing `_` (Enterprise Managed Users) are accepted.

Unchanged for v1 files until you run `ccenv migrate --write`: no built-in
agent permission rules are rendered, the gateway token is still exported, and
`gh` does not fail closed (no empty `GH_CONFIG_DIR`). Git HTTPS to github.com
always uses ccenv's credential helper, though, so a missing gh token now fails
`git push` instead of falling back to your global helper. `ccenv migrate` prints the v2 manifest (keeping
your sops paths, never touching `secrets.enc.yaml`) and lists these switches.

## [1.1.0] - Unreleased (2026-11-11)

Bug fixes for v1 manifests, with no schema change. These fixes are part of
2.0.0 too.

### Fixed

- The "did you mean" hint for a mistyped command works on every Python
  version. It no longer parses argparse's message, whose format changed in
  3.13.
- A `GH_TOKEN` from `secret_env` now gets a git credential helper, so
  `git push` over HTTPS uses that token instead of the global helper's
  account.
- `apiKeyHelper` and `awsAuthRefresh` use absolute paths, and the helper
  runs the base Python interpreter when ccenv runs from a venv. Helpers keep
  working when Claude is started from a GUI app with an empty `PATH`.
  Backend tools (`sops`, `aws`, `gh`, …) are also looked up in common install
  directories and in `CCENV_PATH_EXTRA`.
- `sync` writes each directory for the profile that wins there (longest
  match), using the same matching as `run`.
- `sync` writes files atomically with mode 0600 and creates `.claude/` with
  mode 0700.
- No more tracebacks for a missing `aws`, `gh` or `claude` binary, an unknown
  profile in `sync`, malformed settings JSON, or `"env": null`.
- A string `claude_args` is split like a shell command line instead of
  character by character.
- Env and `secret_env` variable names are validated, so a manifest key can no
  longer inject shell code into `ccenv shell` output.
- sops decrypts once per launch instead of once or twice per secret.
- `ccenv secret ENV KEY --set` passes the value to sops on stdin
  (`--value-stdin`) when sops supports it, so it never appears in `ps`.
- `GIT_CONFIG_*` pairs are appended to an inherited set (direnv,
  devcontainers, an outer session) instead of replacing it.
- CI runs on Ubuntu and macOS, and checks Python 3.9 through uv.
