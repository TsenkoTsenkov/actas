# Changelog

All notable changes to actas (called ccenv before 2.0). Version numbers follow
[Semantic Versioning](https://semver.org/).

## [2.0.0] - Unreleased (2026-11-12)

actas ("act as") applies a profile's LLM provider, cloud accounts, GitHub
account, git identity and secrets to any command or agent, not only to Claude
Code. **v1 manifests keep working unchanged** (see
[Upgrading from v1](#upgrading-from-v1)).

### Renamed to actas

ccenv is now **actas**: `actas exec client-a -- terraform apply` acts as
client-a for one command. The command, the package, the repository
(github.com/TsenkoTsenkov/actas; the old URL redirects), the skill
(`skills/actas`), the direnv function (`use actas`), the refusal line
(`actas-stop:`), the message prefix (`actas:`), the variables (`ACTAS`,
`ACTAS_LEVEL`, `ACTAS_ROOT`, `ACTAS_STATE`, `ACTAS_SESSION`, `ACTAS_AGENT`,
`ACTAS_GIT_BASE`, `ACTAS_PATH_EXTRA`, `ACTAS_FALLBACK_PATH`) and the
directories (`~/.config/actas`, `~/.local/state/actas`, `~/.cache/actas`) all
use the new name. A ccenv install keeps working, with two steps needed right
away: agents are now told to run `actas`, so it must be on your PATH, and an
installed ccenv skill (a link to a checkout's `skills/ccenv` dangles after the
pull) must be replaced with `skills/actas`. `actas doctor` warns about both.
Everything else keeps working as it is:

- **Directories:** while `~/.config/actas` (or `~/.local/state/actas`) does
  not exist and the old `~/.config/ccenv` (or `~/.local/state/ccenv`) does,
  actas uses the old one. `actas doctor` prints the `mv` that moves it. The
  cache is simply rebuilt in `~/.cache/actas`.
- **Variables:** every `ACTAS*` variable actas reads falls back to its
  `CCENV*` spelling when the new one is unset, so shells and synced settings
  files that set `CCENV=<profile>`, `CCENV_ROOT` and so on keep working.
  actas writes only `ACTAS*`; `run`, `exec` and `shell` drop a stale `CCENV`,
  `CCENV_LEVEL` and `CCENV_GIT_BASE` from the child. The agent gate treats `CCENV_ROOT`,
  `CCENV_STATE` and `CCENV_SESSION` exactly like their `ACTAS_` names, so the
  old names are no way around it.
- **Sessions:** while the state directory is still `~/.local/state/ccenv`,
  session records written by a running `ccenv run` are still found.
- **Synced settings files** written by ccenv (v1's `ccenv secret ENV KEY`
  helper, v2's `.../ccenv secret --ref` helper and SessionStart hook,
  `CCENV*` variables, the ledger in the old state directory) are recognised as
  tool-written and rewritten in the new form by the next `actas sync`.
- **The `ccenv` command** stays as a deprecated alias of the same program: a
  second console script in uv/pipx installs and a `bin/ccenv` link in a git
  checkout, so helper strings in files synced before the rename keep working.
  Started from a terminal, it says that the new name is actas. `ccenv secret
  ENV KEY` and `actas secret ENV KEY` both work. `use ccenv` (direnv) and the
  `Bash(ccenv ...)` agent rules stay too.

A uv or pipx install of the 2.0 beta under the old name cannot upgrade in
place (`uv tool upgrade ccenv` fails, and installing actas next to it fails
on the existing `ccenv` command): run `uv tool uninstall ccenv && uv tool
install git+https://github.com/TsenkoTsenkov/actas` (or the same with
`pipx`), which installs both `actas` and the `ccenv` alias.

See [Coming from ccenv](docs/migration.md#coming-from-ccenv) for the steps and
for what you can move or rename at your own pace.

### Added

- **`agents_may_use: ["*"]` and in `defaults:`:** let agents `actas exec`
  every profile at the same or a lower level (never a `confirm` profile),
  for example to push as another GitHub account from any session. A
  profile's own list replaces the default; `[]` opts it out. v1 manifests
  accept the key too, in `defaults:` or an environment. The `not_allowed`
  refusal now says where to add the profile, and `doctor` lists the
  profiles with `"*"`.
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
  `null` on a module isolates it (an empty actas-owned config), and
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
- **Codex and Gemini CLI** (environment level): `actas run -t codex|gemini`,
  per-account homes, `actas login` for Codex, and Gemini's `--approval-mode`
  from the level.
- **Secret schemes:** `sops:`, `keychain:` (macOS), `op://`, `gh:` and `cmd:`.
  `actas secret set REF` stores a value (sops, keychain), and
  `actas secret --ref REF` reads one.
- **Commands:** `exec` (any command with a profile's identity), `status --why`
  (the `use:` chain and where each value comes from), `status --probe`,
  `--json` on read-only commands, `desktop` (with `--print-3p` for Claude
  Desktop's third-party inference setup and `--vscode`), `instructions`
  (agent rules, or a direnv function), and `migrate`. `shell` gains
  `--format fish|direnv|json|env` and `--no-secrets`. `login` gains
  `--module` and `--adc`; `login --all` groups profiles by `[sso-session]` or
  start URL. `doctor` checks the manifest, supports `--strict`, and `--fix`
  tightens actas's own directories to 0700. It also warns about profiles
  without `aws` when `~/.aws/config` has a `[default]` profile, and about
  `.env` files Gemini CLI would load.
- **Two-tier help:** `actas help` shows the eight everyday commands and one
  line of advanced ones; `actas help manifest` and `actas help agents` are
  short primers; `actas --help` lists every option.
- **`actas init`** writes a short version 2 starter and works without sops
  (it suggests `keychain:` or `op://` refs when there is no age key).
- `examples/minimal.yaml`, the manifest from the README quick start.
- **Agent guardrails:** agent detection, a session anchor written by
  `actas run`, an elevation gate for `run`/`exec` (`agents_may_use`, levels,
  `confirm`), an `actas-stop:` line on every refusal with exit codes 3 (needs
  the user) and 4 (credentials expired), human-only commands, an audit log at
  `~/.local/state/actas/audit.jsonl`, and built-in Claude Code permission
  rules per level. `permissions.builtin: false` opts a profile out.
- **SessionStart note:** a short context line tells Claude which profile it is
  in (written for `run` and `sync`).
- **Packaging:** `install.sh` (SHA-256 checked, never sudo, symlink-safe),
  `uv tool install` / `pipx install` from git, and a release workflow with a
  build-provenance attestation. In a uv or pipx install, helper strings
  call the installed `actas` command itself, so they survive upgrades.
- `contrib/direnvrc` and the `skills/actas` Claude Code skill.

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
- **Session records are also kept at a fixed place,** `~/.local/state/actas/sessions/`
  under your passwd home (from the user database; no variable moves it), so an
  agent inside `actas run` that drops `ACTAS_SESSION` and moves
  `XDG_STATE_HOME` or `HOME` is still refused with `override`. That copy is
  searched first, so a record written into a moved `ACTAS_STATE` does not
  beat it. Agent detection runs `/bin/ps` or `/usr/bin/ps` before any `ps` on `PATH`.
- `actas login --module github` with a `gh:<user>@<host>` account no longer
  "switches back" when that account was already active, and `doctor` lists
  each gh account once.
- **`GIT_CONFIG_*` written by `sync`, `shell --format env` and
  `desktop --vscode` carry `ACTAS_GIT_BASE=0`,** so an `actas exec`/`run` from
  bare `claude`, Desktop or the IDE replaces those pairs (including the
  previous profile's credential helper) instead of keeping them.
- **`sync --clean`** now only removes what actas wrote in files that no
  profile syncs any more (listed as `stale`), and keeps a file whose removal
  would let `~/.claude/settings.json`'s `apiKeyHelper` or
  `env.ANTHROPIC_BASE_URL` apply there unless you add `--force`. A plain
  `sync` refreshes a file actas wrote earlier in a nested directory (v1 wrote
  those) instead of suggesting `--clean`. A profile that fails to load is
  skipped, and its files are kept (never treated as stale).
- **`actas login --module codex` with `login: api` needs `codex.home`:** Codex
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
- **Agent gate tightened:** a nested `actas run` started by an agent keeps the
  outer session's `agents_may_use` reach, so hops do not chain. An agent with
  no session (no record, no `ACTAS`) may use only the readonly profile of its
  own working directory. When `actas run` started the agent (`ACTAS_SESSION`)
  but its record is not found, for example after `XDG_STATE_HOME` or `HOME`
  changed, exec is refused with `reason=override`; without a process table,
  `$ACTAS` is not trusted. `ACTAS_ROOT` / `ACTAS_STATE` equal to the default
  place no longer count as an override.
- **An explicit `--cwd` chooses the profile** for `status`, `exec` and `run`,
  also inside agent and direnv sessions (the gate still decides); the session
  profile is the default only when neither a name nor `--cwd` is given.
- **`mismatch` is false** when the directory matches only the `fallback:`
  profile (or nothing); agent rule 2 now says "matched to another profile".
- **`actas exec <p> -- aider`** (or another known agent CLI name) sets
  `ACTAS_AGENT` for the tool, since macOS shows script-based agents as
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
  even when it equals the profile's. Values actas wrote itself (`ACTAS*`, or
  a file it synced before the ledger was lost) are never taken for yours; a v1 subscription file (empty `apiKeyHelper` and
  `ANTHROPIC_BASE_URL`) is adopted like other v1 files.
- **`gh` keeps working in your own direnv/shell:** `shell --no-secrets` (what
  `use actas` runs) no longer points `GH_CONFIG_DIR` at the empty `_off` home
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
  `aws: inherit` only for AWS variables, says "after actas migrate" for v1
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
- Without a YAML parser, `actas init` writes the starter as JSON and says how
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
- The `secret ENV KEY` form that v1-synced helpers call (`ccenv secret ENV
  KEY`) still works. (The binary and config location changed only with the
  rename to actas; see [Renamed to actas](#renamed-to-actas).)

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
  shell's `AWS_PROFILE`, as v1 did (`actas migrate` writes `aws: inherit`
  there to keep that; change it to `aws: null` to isolate). `CLAUDE_CONFIG_DIR`, `OPENAI_API_KEY`,
  `GOOGLE_*` and `ARM_*` are not touched.
- **Subscription mode unsets** `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`
  and `ANTHROPIC_API_KEY` instead of setting them to `""`. Settings also blank
  `CLAUDE_CODE_USE_MANTLE` and `CLAUDE_CODE_USE_ANTHROPIC_AWS`.
- **New variables in sessions:** `ACTAS_LEVEL`, `ACTAS_SESSION`,
  `ACTAS_AGENT` and `ACTAS_GIT_BASE`; in agent sessions also
  `GH_PROMPT_DISABLED=1`, `GIT_TERMINAL_PROMPT=0` and `AWS_PAGER=`. v1's
  `CCENV=<environment>` is now `ACTAS=<profile>` (the rename to actas).
- **Settings files** generated by `run` carry every non-secret variable of the
  session (for example `AWS_DEFAULT_REGION`, the git identity, `ACTAS`), the
  provider blanks and a SessionStart hook.
- **GitHub:** `GITHUB_TOKEN` is set alongside `GH_TOKEN` from
  `secret_env.GH_TOKEN`. With both `github.account` and `secret_env.GH_TOKEN`,
  `GITHUB_TOKEN` is now the PAT. The git credential helper resolves the token
  when git asks for it, and is now also attached to a PAT from `secret_env`.
- **Helper strings** (`apiKeyHelper`, `awsAuthRefresh`, the git helper) use
  absolute, venv-safe paths and the `secret --ref` form. Synced files pick
  them up on the next `actas sync`. The old `actas secret ENV KEY` form still
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
  actas), `ACTAS`/`ACTAS_LEVEL` and the non-interactive variables, so bare
  `claude`, IDE and Desktop sessions in those repos commit and push as the
  profile's account.
  Writes are atomic with mode 0600. A ledger records what actas wrote, so
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
  refused (exit 3, `actas-stop:`) unless the gate allows it. `login`, `sync`,
  `init`, `secret --set`, `aws-handoff`, `migrate --write` and `shell` with
  secrets are human-only.
- **CLI:** `--` after a command other than `run` or `exec` is a usage error
  (exit 2). `doctor` exits 1 when the manifest has errors.
- GitHub logins containing `_` (Enterprise Managed Users) are accepted.

Unchanged for v1 files until you run `actas migrate --write`: no built-in
agent permission rules are rendered, the gateway token is still exported, and
`gh` does not fail closed (no empty `GH_CONFIG_DIR`). Git HTTPS to github.com
always uses actas's credential helper, though, so a missing gh token now fails
`git push` instead of falling back to your global helper. `actas migrate` prints the v2 manifest (keeping
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
