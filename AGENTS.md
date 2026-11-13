# AGENTS.md

Instructions for AI agents (Claude Code, Codex, Gemini, Cursor, Copilot and
others), both when they run inside a session that actas set up and when they
work on this repository.

**What actas is.** actas ("act as") is one Python script,
`bin/actas`. It maps directories to **profiles**. A profile bundles an LLM
provider, cloud accounts (AWS, GCP, Azure), a GitHub account, a git identity
and env vars, and actas applies it to one process: Claude Code, Codex, Gemini
or any command. Secrets are references such as `sops:client-a/token`, resolved
only when needed. The user's config lives outside this repo, in
`~/.config/actas/` (or `$ACTAS_ROOT`).

The block below is what an agent needs inside an actas session. It is the
single source in `bin/actas` (`AGENT_RULES`). `actas instructions` prints it,
and a test keeps this copy identical.

## Credentials (actas)
Credentials here come from actas. They follow the session, not the directory.
1. Before cloud, GitHub, registry or deploy commands, run `actas status --json`. Do not guess the profile from the directory name.
2. If `mismatch` is true, this directory is matched to another profile: do not run cloud, GitHub or deploy commands here with this session's credentials.
3. To use another profile, run `actas exec <profile> --reason "<why>" -- <one command>`. It runs only if the user allowed that profile for agents.
4. If actas prints a line starting with `actas-stop:`, it refused. Show the user the `ask_user` text exactly, then stop and wait. Do not retry, and do not change ACTAS, ACTAS_ROOT, HOME or the directory to get around it. The user runs the command in their own terminal, outside this session. Judge refusals by that line, not by the exit code: `actas exec` returns the command's own exit code.
5. `actas-stop: code=expired`: ask the user to run `actas login <profile>` in their terminal. Never run `aws sso login`, `gcloud auth login`, `az login` or `gh auth login` yourself.
6. In a readonly profile, prepare changes (branch, diff, `terraform plan`) and leave push, apply and deploy to the user.
7. Never print or read secrets: no `actas shell`, `actas secret`, `git credential fill`, `sops -d`, `gh auth token`, and no reading ~/.config/actas, ~/.aws/sso or tool credential files. Never switch accounts globally (`gh auth switch`, `az account set`, `gcloud config set`, `kubectl config use-context`, `aws configure set`).
8. Without actas (for example a cloud session), ask the user to run `actas aws-handoff` on their machine and paste the result. Never ask for long-lived keys.

### Decision flow

```text
Need cloud, GitHub, registry or deploy access?
└─ actas status --json
   ├─ profile is null    -> no profile covers this directory and this
   │                        session has none. Do not run remote commands;
   │                        tell the user (`hint` says what to add).
   ├─ session.profile is null -> this session has no actas profile; tell
   │                        the user (start the agent with `actas run`). Only
   │                        this directory's readonly profile works with
   │                        `actas exec`.
   ├─ "mismatch": true   -> this directory is matched to another profile.
   │                        Do not run remote commands here; tell the user.
   │                        (A directory that only the fallback profile
   │                        covers is no mismatch.)
   ├─ right profile and session.profile set
   │                     -> run the command normally.
   └─ need another profile
      └─ actas exec <profile> --reason "<why>" -- <one command>
         ├─ first stderr line starts with "actas-stop:"
         │    code=needs_user -> show the ask_user text exactly, stop, wait.
         │    code=expired    -> ask the user to run `actas login <profile>`.
         └─ no such line     -> it ran; the exit code is the command's own.
actas not installed here (cloud session)?
└─ ask the user to run `actas aws-handoff` on their machine and paste the result.
```

`actas exec` with no profile uses this session's profile (never the current
directory's). Without a session it falls back to the current directory's
profile, which runs only if that profile is readonly. Changing directory does
not change your credentials. An explicit `--cwd <dir>` is different: it
selects the profile of `<dir>` (for `status`, `exec` and `run`), and the gate
still decides whether you may use it.

### Safe, ask, never

**Safe:** read-only, no secrets in the output.

| Command | What it does |
|---|---|
| `actas status --json` (alias `whoami`) | The session's profile, the cwd's profile, `mismatch`, identities, agent signals |
| `actas status --why` | The `use:` chain, where each value comes from, and how many agent rules apply (`--json` lists them) |
| `actas status --probe` | Also checks each login over the network. Exits 4 with `actas-stop: code=expired` if one has expired. |
| `actas list [--json]`, `actas list --check`, `actas list --names` | Profiles and roles; `--check` also probes AWS logins and gateway tokens (status only, never values); `--names` prints only the names |
| `actas doctor` (without `--fix`) | Tool and manifest checks. `actas doctor --fix` changes directory modes: a human runs it. |
| `actas help`, `actas help <cmd>`, `actas instructions`, `actas completion <shell>` | Static text |
| `actas desktop [profile]`, `actas migrate` (no `--write`) | Print setup text. Refs are names, never values. |

**Ask:** the user approves the tool call, then actas's own gate decides.

| Command | Notes |
|---|---|
| `actas exec <profile> --reason "<why>" -- <cmd>` | Runs if `<profile>` is this session's profile, or is listed in its `agents_may_use`, is at the same or a lower level, and has no `confirm`. Anything else is refused with `actas-stop:`. |
| `actas run [profile]` | Starts a new agent session. It goes through the same gate. Rarely what you want inside a session. |

**Never:** actas refuses some of these with exit 3. The rest are forbidden by the rules above.

| Command | Why |
|---|---|
| `actas login`, `actas sync`, `actas init`, `actas aws-handoff`, `actas secret set`, `actas secret ... --set`, `actas migrate --write`, `actas shell` (with secrets) | Human-only. actas refuses them in agent context (`reason=human_only`). |
| `actas shell`, `actas secret`, `git credential fill`, `sops -d`, `gh auth token`, `op read`, `security find-generic-password` | They print secrets into your transcript. |
| Reading `~/.config/actas`, `~/.aws/sso`, `~/.config/gh/hosts.yml` or other credential files | Same. Claude sessions with actas settings (`run` or `sync`, v2 manifest) deny reading the age key, the AWS SSO/CLI caches, gh `hosts.yml` and managed-home credential files, and deny editing actas's config and state. |
| `aws sso login`, `gcloud auth login`, `az login`, `gh auth login` | A human logs in, through `actas login`. |
| `gh auth switch`, `az account set`, `gcloud config set`, `kubectl config use-context`, `aws configure set` | Global switches change every other session on the machine. |
| Setting `ACTAS`, `ACTAS_ROOT`, `ACTAS_STATE`, `XDG_STATE_HOME` or `HOME`, or `cd`-ing to get past a refusal | Circumvention, which the rules forbid. In a session started by `actas run` it does not work either: actas refuses with `reason=override` when the config or state directory changed or the session's record cannot be found. (Same-user code that forges or deletes actas's state files can still get around it; see SECURITY.md.) |

### Reading a refusal

```text
actas-stop: code=needs_user reason=confirm profile=client-a-admin
actas: refused: client-a-admin requires a typed confirmation, which only a human in a terminal can give
actas: show the user this ask_user text exactly, then stop:
The agent wants to run `terraform apply` as actas profile client-a-admin (power; aws:client-a-admin, gh:ada-client-a).
Agent's reason: "apply the reviewed plan"
If you agree, run it yourself in a separate terminal (not inside the agent session):
  actas exec client-a-admin -- terraform apply
```

Relay the lines after "show the user this ask_user text exactly" word for
word, then stop. With `--json`, the same `ask_user` text is in the JSON on
stdout. Exit codes: 3 means needs the user, 4 means expired credentials. But
`exec` and `run` pass through the command's own exit code (terraform returns
2, many CLIs return 3 to 5), so trust the `actas-stop:` line, not the number.

A command you type with `!` inside Claude Code runs in Claude's Bash tool, so
actas treats it as agent context too. Elevated commands belong in a separate
terminal.

## Helping a user set up actas

The four rules of the manifest:

1. **Later layers win, maps merge.**
2. **`null` means off: the tool sees no account.**
3. **Compose upward:** a profile is at least as privileged as what it uses.
4. **A secret is always `scheme:path`** (`sops:`, `keychain:`, `op://`, `gh:`, `cmd:`).

How to help:

- Ask the user before you edit `~/.config/actas/environments.yaml`, and ask
  them for the real directory paths, AWS profile names
  (`aws configure list-profiles`) and GitHub accounts (`gh auth status`).
  Never guess them. See [docs/manifest.md](docs/manifest.md) and
  [examples/](examples/).
- Never handle secret values. The user stores them in their own terminal with
  `actas secret set <ref>`. It prompts, or reads stdin, so the value never
  passes through you.
- `actas init`, `actas login` and `actas sync` are human-only: tell the user
  to run them.
- Check the result with `actas doctor`, `actas list` and
  `actas --cwd <project> status --why`.
- For agent behaviour (levels, `confirm`, `agents_may_use`, deny rules), see
  [docs/agents.md](docs/agents.md).

## Working on this repository

- **One file, standard library only.** `bin/actas` runs on Python 3.9+.
  PyYAML is optional, and so is every external tool (sops, aws, gh, op,
  gcloud, az, codex, gemini). They are called as subprocesses, and only when a
  feature needs them. A new runtime dependency needs a strong reason.
- **Tables first.** Breadth lives in data tables near the top of the file:
  `BODY_KEYS`/`MODULE_KEYS`/`TOOL_KEYS`, `REF_RES`, `OWNS`, `EMITS`,
  `NULL_VARS`, `AGENT_ENV`, `CRED_PATHS`, `PRESETS`, `INTENT_RULES`,
  `ANCESTOR_AGENTS`, `REASONS`. Add a row before you add a code path.
- **Python 3.9:** no `match` statements, no `X | Y` type unions, no nested
  quotes of the same type inside f-strings.
- **Secrets:**
  - Never write a secret to disk in the clear, never put one in argv where
    stdin works, and never print one except in the explicit secret-read
    commands.
  - Generated files go through `write_private()` (atomic, 0600).
  - Everything interpolated into a shell string goes through `shlex.quote`.
  - Names that reach a sops path go through `check_name()`.
- **`AGENT_RULES` has one source:** the constant in `bin/actas`. If you change
  it, paste the new text into this file and into `skills/actas/SKILL.md`. The
  test in `tests/test_agent_rules.py` fails until all three match.
- **The old name is legacy only.** actas was called ccenv. New code reads
  variables through `env_get()` / `env_any()` (`ACTAS_X`, then the old
  `CCENV_X`) and default directories through `legacy_dir()`, and writes only
  the new names. The gate treats both spellings alike. `tests/test_legacy_compat.py`
  covers the fallbacks.
- **Refusals** go through `stop()`. It prints the `actas-stop:` line first on
  stderr and exits 3 or 4. Never add a bypass flag or variable: anything an
  agent can set is no bypass.
- **CLI:** add new options with a `help=` string so they appear in
  `actas --help` and `actas help <cmd>`. A new command also goes into
  `EVERYDAY` or `ADVANCED`, the two tiers `actas help` shows. Give it a
  `description=` and `examples=`. Usage errors exit 2: an unknown command
  prints the full help, a bad argument to a known command prints only that
  command's usage line and one hint (`Parser.error`).
- **Tests:**
  - Use stdlib `unittest` with the `Sandbox` class in
    `tests/actas_testlib.py`. Each test gets a temp `HOME`, `ACTAS_ROOT`,
    `ACTAS_STATE` and XDG dirs, and a `PATH` of fake `gh`, `aws`, `sops`,
    `security`, `op`, `gcloud`, `az`, `claude`, `codex`, `gemini` and `ps`.
  - Tests run a sandbox copy of `bin/actas` (`self.bin`, also what `self.m`
    is loaded from) in which the fixed-place seams, `PS_BINS` (the system
    `ps`) and `USER_STATE` (the passwd home's state dir, holding the session
    records), name the fake `ps` and a temp dir, and `PS_NATIVE = True` (the
    macOS `sysctl` ancestor reader, which the fake `ps` could not mask)
    becomes `False`. Keep those lines exactly as `SEAMS` in the testlib
    spells them; the shipped binary has no override.
  - No test may reach a real login, the developer's `~/.config/actas` or
    `~/.local/state/actas`.
  - Run: `python3 -m unittest discover -s tests -v`. Check 3.9 with
    `uv run --no-project --python 3.9 python -m unittest discover -s tests`.
  - Add a test for every new behaviour.
- **You are an agent too.** actas detects your session while you work here.
  The fake `ps` masks agent process names, so the suite behaves the same
  inside an agent as in CI. Do not run `actas login`, `sync`, `secret`,
  `shell` or `aws-handoff` against the user's real config.
- **Placeholders only** in code, tests, docs and examples: `client-a`,
  `example.com`, `Ada Example`. Never use real organisations, accounts or
  paths.
- **Docs describe the code.** If something is not yet verified against the
  real tool, say so.
- **Releasing:** set `__version__` in `bin/actas` and the CHANGELOG date, then
  push the tag `v<__version__>` (the release workflow checks they match). A
  tag with a letter (`v2.0.0b1`) becomes a GitHub prerelease, which
  `install.sh`'s default `latest` never installs, so the first public v2 tag
  must be a stable one such as `v2.0.0`.
