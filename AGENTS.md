# AGENTS.md

Instructions for AI agents (Claude Code, Codex, Gemini, Cursor, Copilot and
others), both when they run inside a session that ccenv set up and when they
work on this repository.

**What ccenv is.** ccenv ("credential context env") is one Python script,
`bin/ccenv`. It maps directories to **profiles**. A profile bundles an LLM
provider, cloud accounts (AWS, GCP, Azure), a GitHub account, a git identity
and env vars, and ccenv applies it to one process: Claude Code, Codex, Gemini
or any command. Secrets are references such as `sops:client-a/token`, resolved
only when needed. The user's config lives outside this repo, in
`~/.config/ccenv/` (or `$CCENV_ROOT`).

The block below is what an agent needs inside a ccenv session. It is the
single source in `bin/ccenv` (`AGENT_RULES`). `ccenv instructions` prints it,
and a test keeps this copy identical.

## Credentials (ccenv)
Credentials here come from ccenv. They follow the session, not the directory.
1. Before cloud, GitHub, registry or deploy commands, run `ccenv status --json`. Do not guess the profile from the directory name.
2. If `mismatch` is true, this directory is matched to another profile: do not run cloud, GitHub or deploy commands here with this session's credentials.
3. To use another profile, run `ccenv exec <profile> --reason "<why>" -- <one command>`. It runs only if the user allowed that profile for agents.
4. If ccenv prints a line starting with `ccenv-stop:`, it refused. Show the user the `ask_user` text exactly, then stop and wait. Do not retry, and do not change CCENV, CCENV_ROOT, HOME or the directory to get around it. The user runs the command in their own terminal, outside this session. Judge refusals by that line, not by the exit code: `ccenv exec` returns the command's own exit code.
5. `ccenv-stop: code=expired`: ask the user to run `ccenv login <profile>` in their terminal. Never run `aws sso login`, `gcloud auth login`, `az login` or `gh auth login` yourself.
6. In a readonly profile, prepare changes (branch, diff, `terraform plan`) and leave push, apply and deploy to the user.
7. Never print or read secrets: no `ccenv shell`, `ccenv secret`, `git credential fill`, `sops -d`, `gh auth token`, and no reading ~/.config/ccenv, ~/.aws/sso or tool credential files. Never switch accounts globally (`gh auth switch`, `az account set`, `gcloud config set`, `kubectl config use-context`, `aws configure set`).
8. Without ccenv (for example a cloud session), ask the user to run `ccenv aws-handoff` on their machine and paste the result. Never ask for long-lived keys.

### Decision flow

```text
Need cloud, GitHub, registry or deploy access?
└─ ccenv status --json
   ├─ "mismatch": true   -> this directory is matched to another profile.
   │                        Do not run remote commands here; tell the user.
   │                        (A directory that only the fallback profile
   │                        covers is no mismatch.)
   ├─ right profile      -> run the command normally.
   └─ need another profile
      └─ ccenv exec <profile> --reason "<why>" -- <one command>
         ├─ first stderr line starts with "ccenv-stop:"
         │    code=needs_user -> show the ask_user text exactly, stop, wait.
         │    code=expired    -> ask the user to run `ccenv login <profile>`.
         └─ no such line     -> it ran; the exit code is the command's own.
ccenv not installed here (cloud session)?
└─ ask the user to run `ccenv aws-handoff` on their machine and paste the result.
```

`ccenv exec` with no profile uses this session's profile, never the profile
of the current directory. Changing directory does not change your
credentials. An explicit `--cwd <dir>` is different: it selects the profile
of `<dir>` (for `status`, `exec` and `run`), and the gate still decides
whether you may use it.

### Safe, ask, never

**Safe:** read-only, no secrets in the output.

| Command | What it does |
|---|---|
| `ccenv status --json` (alias `whoami`) | The session's profile, the cwd's profile, `mismatch`, identities, agent signals |
| `ccenv status --why` | The `use:` chain, where each value comes from, and how many agent rules apply (`--json` lists them) |
| `ccenv status --probe` | Also checks each login over the network. Exits 4 with `ccenv-stop: code=expired` if one has expired. |
| `ccenv list [--json]`, `ccenv list --check` | Profiles and roles; `--check` also probes AWS and secret availability (status only, never values) |
| `ccenv doctor` | Tool and manifest checks |
| `ccenv help`, `ccenv help <cmd>`, `ccenv instructions` | Static text |
| `ccenv desktop [profile]`, `ccenv migrate` (no `--write`) | Print setup text. Refs are names, never values. |

**Ask:** the user approves the tool call, then ccenv's own gate decides.

| Command | Notes |
|---|---|
| `ccenv exec <profile> --reason "<why>" -- <cmd>` | Runs if `<profile>` is this session's profile, or is listed in its `agents_may_use`, is at the same or a lower level, and has no `confirm`. Anything else is refused with `ccenv-stop:`. |
| `ccenv run [profile]` | Starts a new agent session. It goes through the same gate. Rarely what you want inside a session. |

**Never:** ccenv refuses some of these with exit 3. The rest are forbidden by the rules above.

| Command | Why |
|---|---|
| `ccenv login`, `ccenv sync`, `ccenv init`, `ccenv aws-handoff`, `ccenv secret set`, `ccenv secret ... --set`, `ccenv migrate --write`, `ccenv shell` (with secrets) | Human-only. ccenv refuses them in agent context (`reason=human_only`). |
| `ccenv shell`, `ccenv secret`, `git credential fill`, `sops -d`, `gh auth token`, `op read`, `security find-generic-password` | They print secrets into your transcript. |
| Reading `~/.config/ccenv`, `~/.aws/sso`, `~/.config/gh/hosts.yml` or other credential files | Same. Claude sessions started by ccenv also have deny rules for these. |
| `aws sso login`, `gcloud auth login`, `az login`, `gh auth login` | A human logs in, through `ccenv login`. |
| `gh auth switch`, `az account set`, `gcloud config set`, `kubectl config use-context`, `aws configure set` | Global switches change every other session on the machine. |
| Setting `CCENV`, `CCENV_ROOT`, `CCENV_STATE`, `XDG_STATE_HOME` or `HOME`, or `cd`-ing to get past a refusal | Circumvention, which the rules forbid. In a session started by `ccenv run` it does not work either: ccenv refuses with `reason=override` when the config or state directory changed or the session's record cannot be found. (Same-user code that forges or deletes ccenv's state files can still get around it; see SECURITY.md.) |

### Reading a refusal

```text
ccenv-stop: code=needs_user reason=confirm profile=client-a-admin
ccenv: refused: client-a-admin requires a typed confirmation, which only a human in a terminal can give
ccenv: show the user this ask_user text exactly, then stop:
The agent wants to run `terraform apply` as ccenv profile client-a-admin (power; aws:client-a-admin, gh:ada-client-a).
Agent's reason: "apply the reviewed plan"
If you agree, run it yourself in a separate terminal (not inside the agent session):
  ccenv exec client-a-admin -- terraform apply
```

Relay the lines after "show the user this ask_user text exactly" word for
word, then stop. With `--json`, the same `ask_user` text is in the JSON on
stdout. Exit codes: 3 means needs the user, 4 means expired credentials. But
`exec` and `run` pass through the command's own exit code (terraform returns
2, many CLIs return 3 to 5), so trust the `ccenv-stop:` line, not the number.

A command you type with `!` inside Claude Code runs in Claude's Bash tool, so
ccenv treats it as agent context too. Elevated commands belong in a separate
terminal.

## Helping a user set up ccenv

The four rules of the manifest:

1. **Later layers win, maps merge.**
2. **`null` means off: the tool sees no account.**
3. **Compose upward:** a profile is at least as privileged as what it uses.
4. **A secret is always `scheme:path`** (`sops:`, `keychain:`, `op://`, `gh:`, `cmd:`).

How to help:

- Ask the user before you edit `~/.config/ccenv/environments.yaml`, and ask
  them for the real directory paths, AWS profile names
  (`aws configure list-profiles`) and GitHub accounts (`gh auth status`).
  Never guess them. See [docs/manifest.md](docs/manifest.md) and
  [examples/](examples/).
- Never handle secret values. The user stores them in their own terminal with
  `ccenv secret set <ref>`. It prompts, or reads stdin, so the value never
  passes through you.
- `ccenv init`, `ccenv login` and `ccenv sync` are human-only: tell the user
  to run them.
- Check the result with `ccenv doctor`, `ccenv list` and
  `ccenv --cwd <project> status --why`.
- For agent behaviour (levels, `confirm`, `agents_may_use`, deny rules), see
  [docs/agents.md](docs/agents.md).

## Working on this repository

- **One file, standard library only.** `bin/ccenv` runs on Python 3.9+.
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
- **`AGENT_RULES` has one source:** the constant in `bin/ccenv`. If you change
  it, paste the new text into this file and into `skills/ccenv/SKILL.md`. The
  test in `tests/test_agent_rules.py` fails until all three match.
- **Refusals** go through `stop()`. It prints the `ccenv-stop:` line first on
  stderr and exits 3 or 4. Never add a bypass flag or variable: anything an
  agent can set is no bypass.
- **CLI:** add new options with a `help=` string so they appear in
  `ccenv --help` and `ccenv help <cmd>`. A new command also goes into
  `EVERYDAY` or `ADVANCED`, the two tiers `ccenv help` shows. Usage errors
  print help and exit 2.
- **Tests:**
  - Use stdlib `unittest` with the `Sandbox` class in
    `tests/ccenv_testlib.py`. Each test gets a temp `HOME`, `CCENV_ROOT`,
    `CCENV_STATE` and XDG dirs, and a `PATH` of fake `gh`, `aws`, `sops`,
    `security`, `op`, `gcloud`, `az`, `claude`, `codex`, `gemini` and `ps`.
  - Tests run a sandbox copy of `bin/ccenv` (`self.bin`, also what `self.m`
    is loaded from) in which the two fixed-place seams, `PS_BINS` (the
    system `ps`) and `USER_SESSIONS` (session records under the passwd
    home), name the fake `ps` and a temp dir. Keep those two lines exactly as
    `SEAMS` in the testlib spells them; the shipped binary has no override.
  - No test may reach a real login, the developer's `~/.config/ccenv` or
    `~/.local/state/ccenv`.
  - Run: `python3 -m unittest discover -s tests -v`. Check 3.9 with
    `uv run --no-project --python 3.9 python -m unittest discover -s tests`.
  - Add a test for every new behaviour.
- **You are an agent too.** ccenv detects your session while you work here.
  The fake `ps` masks agent process names, so the suite behaves the same
  inside an agent as in CI. Do not run `ccenv login`, `sync`, `secret`,
  `shell` or `aws-handoff` against the user's real config.
- **Placeholders only** in code, tests, docs and examples: `client-a`,
  `example.com`, `Ada Example`. Never use real organisations, accounts or
  paths.
- **Docs describe the code.** If something is not yet verified against the
  real tool, say so.
- **Releasing:** set `__version__` in `bin/ccenv` and the CHANGELOG date, then
  push the tag `v<__version__>` (the release workflow checks they match). A
  tag with a letter (`v2.0.0b1`) becomes a GitHub prerelease, which
  `install.sh`'s default `latest` never installs, so the first public v2 tag
  must be a stable one such as `v2.0.0`.
