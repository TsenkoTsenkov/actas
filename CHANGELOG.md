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

### Beta review: fixes and CLI improvements

A review of the whole tool after the rename. Items that can change how an
existing manifest behaves: the glob matching fix, the `claude.settings`
permission lists, the git variables now cleared, and the YAML 1.2 value rules
for version 2 files.

**CLI**

- a mistyped option or extra argument for a known command prints that
  command's usage line and one hint (`did you mean: --json?`, where `--` goes
  for `run`/`exec`, or `actas help <cmd>`) instead of the full help. An
  unknown command still shows the full help with a did-you-mean.
- `actas <profile> -- cmd` is short for `actas exec <profile> -- cmd` in your
  own terminal; command names win over profile names. Inside an agent session
  it is a usage error that names the `actas exec` form, so the Switch ask rule
  still applies. A bare or mistyped profile name points to `actas exec`.
- `actas completion bash|zsh|fish` prints a tab-completion script for
  commands, options and profile names; `actas list --names` prints one profile
  name per line.
- `--no-color` works after the command too (`actas status --no-color`).
- every command's help (`actas help <cmd>`) has a description and examples;
  `run`'s usage shows `[-- tool args...]`.
- `*` after a name marks the profile matching this directory and `+` this
  session's profile, without widening the lines; `--json` is unchanged.
- `actas init` takes the starter profile's git identity from your global git
  config. Without one, the `git:` line stays commented out, so the starter no
  longer puts `Your Name <you@example.com>` into commits.
- `actas doctor` checks what the manifest names against this machine: `gh:`
  accounts that `gh auth status` does not list (per host, also inside a
  session), `aws.profile` values that are in neither the AWS config file nor
  the credentials file, and missing tools for refs and tools in use
  (`secrets.enc.yaml`, sops and an age key for `sops:`, `op` for `op://`,
  `security` for `keychain:`, `cmd:` programs, codex or gemini for `tool:`).
  It also flags git identities at reserved example domains (`example.com`,
  `*.example`). These are warnings, so `--strict` fails on them. It reads no
  secret and makes no new network calls.
- `actas list --check` reads a gateway token shared by several profiles once,
  not once per profile (one op or Keychain prompt).
- `actas login --all` exits 1 and names the SSO portals whose login failed or
  did not take, after trying the rest. It used to always exit 0.
- Help and AGENTS.md: `list --check` probes AWS logins and gateway tokens. The
  old text said 'secret availability'.

**Correctness**

- Composition: `claude.settings.permissions.deny` and `.ask` are now unioned
  through `use:` and sticky, like `permissions.deny`/`ask`. Before, a
  profile's own list silently replaced a role's rules. A manifest that relied
  on a profile replacing or dropping a role's `claude.settings` deny/ask list
  (including `claude.settings: null` or `claude: null`) now gets an error that
  names the layer. `claude.settings.permissions.allow` still replaces, so
  composing never allows more than one layer says.
- Directory matching: a glob pattern now also matches the directories below
  the ones it matches, as documented (`~/code/*/infra` covers
  `~/code/a/infra/modules`). Before, those subdirectories fell through to a
  broader profile, possibly one with a different level and credentials. This
  applies to v1 manifests too: v1 had the same bug, and patterns that end in
  `*` are unaffected.
- No-match hint: outside every `match`, actas now names the directory and
  suggests a pattern that matches it: the git repository's top directory under
  your home when there is one, otherwise the directory. The old hint suggested
  `~/code/x/**`, which does not match `~/code/x` itself.
- `actas status` (and `--json`) outside every `match`, with no session and no
  `fallback`, now reports no profile instead of failing: `"profile": null`
  plus a `hint`, exit 0. `status --why`/`--probe`, `run` and `exec` still fail
  with exit 1. Agents are told that a null `profile` means: do not run remote
  commands, tell the user.
- Fixed: with `github.host` set to an enterprise host, a `gh:<login>` ref
  without `@host` now means `gh:<login>@<host>`. The token is read for that
  host and the git credential helper names it. Before, actas read the
  github.com account's token and sent it to the enterprise host.
- Fixed: git's `GIT_AUTHOR_NAME/EMAIL`, `GIT_COMMITTER_NAME/EMAIL` and
  `GIT_SSH_COMMAND` from your shell are now cleared when the manifest uses
  `git`. Before, they silently overrode the profile's
  `git.name`/`email`/`ssh_key`. Keep one with `inherit: [VAR]`, or all of them
  with `git: inherit`. v1 manifests clear only the name and email variables
  for what the environment sets.
- Fixed: more identity overrides are cleared when their module is used:
  `CLOUDSDK_CORE_ACCOUNT`, `CLOUDSDK_AUTH_ACCESS_TOKEN_FILE` and
  `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE` (gcp); the Azure SDK service-
  principal variables `AZURE_CLIENT_ID/SECRET/TENANT_ID/...` and
  `ARM_ACCESS_KEY`, `ARM_OIDC_TOKEN`, `ARM_CLIENT_CERTIFICATE_*` (azure); and
  `AWS_ROLE_ARN`, `AWS_ROLE_SESSION_NAME`, `AWS_WEB_IDENTITY_TOKEN_FILE`,
  `AWS_CONTAINER_CREDENTIALS_*` and `AWS_CONTAINER_AUTHORIZATION_TOKEN*`
  (aws). This applies to v2 manifests only.
- Fixed: reading a `gh:` ref from inside another profile's session (for
  example `actas exec a` from an `actas run b` session where b uses
  `github.home`) no longer uses that session's managed `GH_CONFIG_DIR`. Any
  home under actas's state dir is ignored, not only `_off`.

**Security**

- Security: refusals no longer repeat credential values an agent's shell
  expanded into the command. Values of credential variables (GH_TOKEN,
  ANTHROPIC_API_KEY, AWS_SECRET_ACCESS_KEY..., every manifest secret_env name,
  and names containing TOKEN/SECRET/PASSW/API_KEY/ACCESS_KEY) show as <$NAME>,
  and token-shaped strings as <token>. This applies in the ask_user text, the
  suggested command, the --json `command`, human-only refusals and the audit
  log's cmd0, with a Note asking the user to put the right value back.
  argv_sha256 still hashes the real argv.
- Security: the built-in secrets deny rules now also match actas/ccenv by any
  path (`Bash(*/actas secret*)`...) and the exact helper command lines actas
  writes into apiKeyHelper and GIT_CONFIG_VALUE_* (python plus checkout path,
  or the package's console script).
- Security: Gemini credential stores (~/.gemini/oauth_creds.json, mcp-oauth-
  tokens.json, .env, and the same files in gemini homes) are Read-denied for
  Claude Code when the manifest uses Gemini.
- Security: the ask_user 'runs a shell or interpreter' warning now also covers
  package runners and wrappers (npx, uvx, bunx, deno, make, awk, sudo, nohup,
  ssh...) and run/exec subcommands (pipx run, uv run, go run, cargo run, npm
  exec, docker run/exec, kubectl exec/run/debug...).
- Security: the agent's `--reason` is redacted the same way in the ask_user
  text and the --json `agent_reason`.

**Speed**

- Faster on macOS: agent detection reads the ancestor processes from the
  kernel (sysctl) instead of running `ps -A` over the whole process table.
  Status, exec, run, the direnv hook and git-credential get noticeably faster
  (status about 46% faster here, direnv about 50%). Each row is exactly what
  ps prints. Other users' processes such as `login` still come from the system
  `/bin/ps -p`, and anything unexpected falls back to `ps -A`, so detection is
  unchanged. Linux still uses `ps -A`.
- Stale session records are pruned by checking whether the pid is still alive,
  not whether it appears in the full process table.
- `actas status --probe` runs its aws, gcloud, az and gh checks in parallel.
  `actas run` checks the AWS login while it resolves secrets, not before
  (`--login` is unchanged).
- Manifests are parsed with PyYAML's libyaml loader when available (about 10x
  faster). Error messages still show the offending line and caret.

**Install, packaging and manifests**

- Manifest values are read the same way with PyYAML and with yq (close to YAML
  1.2). Only `true`/`false` are booleans, and a number counts as a number only
  when it reads back exactly as written. `0755`, `012345678901`, `0x1F`,
  `1_000`, `1:30`, `2024-01-01`, `1.10`, `yes`, `on` and `NO` are passed as
  written; before, they could be silently converted (octal, a dropped leading
  zero, sexagesimal, dates) or rejected on one install and accepted on
  another. `true`/`false` in `env` are still errors. Version 1 files keep
  their old YAML 1.1 reading.
- New `actas setup skill [NAME] [--dir DIR]` and `actas setup direnv [--file
  FILE]` (human-only, with `--dry-run` and `--force`) install the Claude Code
  skill and the direnv function that this actas carries
  (`~/.claude/skills/actas/SKILL.md`, `~/.config/direnv/lib/actas.sh`).
  uv/pipx and install.sh users no longer need a checkout for them. `actas
  instructions --skill [NAME]` prints a skill's SKILL.md. doctor points at
  `actas setup skill` and reports a copied skill from another version.
- New `actas sync --clean --all` removes what actas wrote in every synced file
  and writes nothing. It works without a manifest and is meant to be run
  before uninstalling. The README has an Uninstall section for uv, pipx,
  install.sh and a checkout.
- `actas --version` names the install: `(package)` for uv/pipx (plus the git
  commit when one was recorded), or `(git <commit>[+changes])` for a checkout.
  A copied single file prints the version alone.
- install.sh no longer replaces a uv or pipx install's `actas` link, even with
  `--force`; it says to use `uv tool upgrade` or to uninstall first. It no
  longer warns about a `ccenv` link that is already an alias of actas.
- CI: one test leg and the release job install PyYAML, so the README and
  examples/ manifests are checked. The v1 wildcard tests no longer need a YAML
  parser.
- A version 2 manifest that writes `yes`/`no`/`on`/`off` for a true/false key
  (`confirm: yes`) gets an error saying to write true or false; in
  `claude.settings` such words get a warning, since they reach Claude as text.

**Robustness**

- A file actas cannot read or write (a read-only repo, an unwritable cache or
  state directory) now ends in one `actas: <path>: <reason>` line with exit 1
  instead of a Python traceback; set `ACTAS_DEBUG=1` to see the traceback. A
  closed stdout pipe (`actas list | head`) exits quietly.
- `actas sync` skips a repo it cannot write (warning `skip <file>: cannot
  write it`), still writes the other repos and exits 1. The ledger is now
  saved even when a repo fails, so files written earlier in the run stay
  recorded and `sync --clean` can still remove them. A file `--clean` cannot
  change keeps its ledger entry and is retried next time.
- `actas aws-handoff` dies with a clear message when `aws sts get-session-
  token` or `export-credentials` prints something unusable. It still never
  hands over long-lived keys.
- `actas secret ENV KEY` (the v1 form) now adds the same fix-it hint as
  `--ref` when a secret is missing: `(run: actas secret ENV KEY --set)`.
- Per-invocation state (warnings, held stderr lines, the decrypted-secrets
  cache, output mode) is reset at the start of every in-process `main()` call.

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
