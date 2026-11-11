# ccenv

**Credential context env: the right accounts for every project, in every terminal and agent.**

You work for several clients, on personal projects and on open source. Each
context has its own LLM provider (a claude.ai subscription, a company gateway,
Bedrock, Vertex), its own AWS, GCP or Azure account, its own GitHub account and
git identity, and its own tokens. ccenv keeps all of that in one manifest. It
picks the profile from the directory you are in and applies it to **one
process**: Claude Code, Codex, Gemini CLI, `terraform`, `aws`, or any other
command.

```console
$ cd ~/code/client-a/api
$ ccenv run
ccenv: cleared from your shell: ANTHROPIC_API_KEY, AWS_ACCESS_KEY_ID
ccenv: client-a · standard · gateway · aws:client-a-dev · gh:ada-client-a

$ ccenv exec -- aws sts get-caller-identity     # any command, same identity
$ ccenv run -t codex                              # another agent, same identity
```

- **Works with any CLI or agent.** Claude Code is first-class: providers,
  settings, permission rules, and Desktop and IDE support. Codex and Gemini
  CLI get their own login home per account. Everything else runs through
  `ccenv exec`.
- **Nothing global changes when you run a profile.** No `gh auth switch`, no
  edits to `~/.gitconfig` or `~/.aws/config`. Two terminals can be two different people in two
  different accounts at the same time.
- **No secrets in the clear.** The manifest holds references such as
  `keychain:ccenv/client-a` or `sops:client-a/token`. They are resolved at
  launch or when used, and never written to a file in clear text.
- **Agent-aware.** Agents get rules tied to the profile's privilege level, and
  ccenv refuses to let an agent switch itself to a more powerful profile.
- **One file, standard library only.** A single Python 3.9+ script. Runs on
  macOS and Linux, and on Windows through WSL.

## Contents

- [Quick start](#quick-start)
- [The four rules](#the-four-rules)
- [Common setups](#common-setups)
- [Roles, levels and permission sets](#roles-levels-and-permission-sets)
- [What ccenv supports](#what-ccenv-supports)
- [Where it works](#where-it-works)
- [AI agents](#ai-agents)
- [Install](#install)
- [Commands](#commands)
- [Manifest reference](#manifest-reference)
- [Security](#security)
- [Upgrading from v1](#upgrading-from-v1)
- [Development](#development)

## Quick start

This takes about a minute once ccenv is [installed](#install).

**1. Create the config directory.** `ccenv init` writes a short, commented
version 2 starter to `~/.config/ccenv/environments.yaml`. It never overwrites
an existing file, and it works without sops. Without a YAML parser (see the
note after step 4) the starter is written as JSON.

```bash
ccenv init
```

**2. Describe your contexts.** Replace the starter with something like this:

```yaml
version: 2
profiles:
  personal:
    match: ~/code/personal
    git:    { name: Ada Example, email: ada@example.com }
    github: { auth: "gh:ada-personal" }
    aws:    { profile: personal, region: eu-west-1 }
  client-a:
    match: ~/code/client-a
    git:    { name: Ada Example, email: ada@client-a.example }
    github: { auth: "gh:ada-client-a" }
    aws:    { profile: client-a-dev, region: eu-west-1 }
    claude: { provider: gateway, base_url: "https://llm.client-a.example", token: "keychain:ccenv/client-a" }
```

- `match`: the directories this profile applies to, including subdirectories. Globs work, and the longest match wins.
- `github.auth: "gh:<login>"`: an account you already signed in to with `gh auth login`. ccenv reads its token without switching gh's active account.
- `aws.profile`: a profile from `~/.aws/config`.
- `claude`: the default is your claude.ai subscription. Here `client-a` uses the client's LLM gateway, with the token in the macOS Keychain.

**3. Store the token.** You are prompted for it, so it never lands in shell
history or in `ps`.

```bash
ccenv secret set keychain:ccenv/client-a
```

On Linux, use a `sops:`, `op://` or `cmd:` ref instead (see
[secret schemes](#secret-schemes)).

**4. Check, then launch.**

```bash
ccenv status --cwd ~/code/client-a    # which profile applies there, and what it sets
ccenv doctor                          # tools, manifest problems, leaking shell exports
cd ~/code/client-a && ccenv run       # Claude Code as client-a
```

`ccenv status --why` shows where each value comes from. `ccenv list` shows
every profile.

> The manifest is YAML when PyYAML or `yq` (mikefarah's, or the Python `yq`
> that Debian and Ubuntu ship) is installed. Without either, write the same
> file as JSON: `ccenv init` then writes a JSON starter, and `ccenv doctor`
> says which parser is missing. ccenv itself needs nothing but Python.

## The four rules

Everything in the manifest follows four rules:

1. **Later layers win, maps merge.** A profile's own keys override what it
   `use:`s, key by key. Restriction lists such as `permissions.deny` and
   `unset` are combined, never dropped.
2. **`null` means off: the tool sees no account.** `aws: null` points the AWS
   CLI at an empty config, so it cannot fall back to your `[default]` profile.
   `"inherit"` passes your shell's values through instead.
3. **Compose upward.** A profile is at least as privileged as anything it
   uses. A profile that declares `level: readonly` but uses a `power` role is
   an error.
4. **A secret is always `scheme:path`.** For example `sops:client-a/token`,
   `keychain:ccenv/gw`, `op://vault/item/field`, `gh:ada-personal` or
   `cmd:["pass", "show", "x"]`. A bare value where a secret is expected is an
   error.

## Common setups

**Two claude.ai subscriptions.** `claude.home` gives a profile its own Claude
Code login, settings and history (through `CLAUDE_CONFIG_DIR`). Run `/login`
once inside its first session. This works wherever ccenv sets the environment:
`ccenv run`, `ccenv exec`, or a shell after `eval "$(ccenv shell work)"`.
Claude Desktop, the IDE extensions and a bare `claude` started without ccenv's
environment keep the app's single login (one claude.ai account per app; see
[docs/desktop.md](docs/desktop.md)).

```yaml
  work:
    match: ~/code/oss-work
    claude: { home: work }      # ~/.local/state/ccenv/homes/claude/work
```

A new home starts empty: it has none of your main home's user settings,
plugins, MCP servers or `CLAUDE.md`.

**A client with its own gateway, AWS and GitHub.** This is `client-a` in the
quick start. Inside `~/code/client-a`, Claude talks to the gateway, `aws` uses
`client-a-dev`, `gh` and HTTPS `git push` use `ada-client-a`, and commits carry
the client email. A repo cloned over SSH (`git@github.com:…`) pushes with
whatever key `ssh` picks, usually your personal one: add
`git: { ssh_key: ~/.ssh/id_ed25519_client_a }` to the profile, which makes git
use only that key. A stray `AWS_ACCESS_KEY_ID` or `ANTHROPIC_API_KEY` in your
shell cannot leak in: ccenv clears the variables of every module your
manifest uses, unless the profile sets them.

**No cloud account at all.** `aws: null` in a profile makes the AWS CLI see an
empty config.

**Read-only by default, power on demand.** See the next section.

More setups (GitLab, Bitbucket, npm, uv/pip, cargo, Docker, Kubernetes,
Terraform Cloud, Vault, other agents, direnv) are written as roles in
[docs/recipes.md](docs/recipes.md).

## Roles, levels and permission sets

When one client needs more than one identity, split the pieces into **roles**
and combine them with `use:`. A role is a profile fragment that never runs on
its own. A **permission set** is just a role that carries agent rules.

Each profile has a **level**: `readonly`, `standard` (the default) or `power`.
The level says how much the credentials can do. It picks the built-in agent
rules (below) and limits which profiles an agent may switch to.
`confirm: true` makes a human type the profile name before it runs. Agents can
never run a `confirm` profile.

```yaml
version: 2
defaults:
  git: { name: Ada Example, email: ada@example.com }

roles:
  gh-personal-ro:                                   # fine-grained, read-only PAT
    level: readonly
    github: { auth: "sops:personal/gh_ro_pat" }
  aws-personal-ro:
    level: readonly
    aws: { profile: personal-readonly, region: eu-west-1 }
  aws-personal-power:
    level: power
    aws: { profile: personal-poweruser }            # region still comes from aws-personal-ro
  no-pulumi:                                        # a permission set
    permissions: { deny: ["Bash(pulumi up *)", "Bash(pulumi destroy *)"] }

profiles:
  personal:
    match: [~/code/personal, ~/code/sandbox-*]
    use: [gh-personal-ro, aws-personal-ro, no-pulumi]     # level: readonly
  personal-power:                                         # personal, plus AWS PowerUser
    use: [personal, aws-personal-power]                   # level: power
    confirm: true                                         # a human types "personal-power"
```

`personal-power` keeps everything from `personal`: the read-only PAT, the
region, the git identity and the `no-pulumi` rules. Only the AWS profile
becomes PowerUser. It has no `match`, so it never applies by directory. You
use it by name, one command at a time:

```console
$ ccenv exec personal-power -- terraform apply
Elevate this shell -> personal-power (power) to run: terraform apply.
Type the profile name to continue:
```

`ccenv status personal-power --why` shows where each value comes from:

```text
chain     defaults -> gh-personal-ro -> aws-personal-ro -> no-pulumi -> personal -> aws-personal-power -> personal-power
          aws.profile = personal-poweruser (from aws-personal-power, was personal-readonly from aws-personal-ro)
          aws.region = eu-west-1 (from aws-personal-ro)
```

**Built-in agent rules per level.** ccenv renders these as Claude Code
permission rules. "allow" means no rule is added; ccenv never adds allow
rules.

| | readonly | standard | power |
|---|---|---|---|
| edit local files | allow | allow | allow |
| `git push`, `gh pr merge`, `gh release` | deny | ask | allow |
| `terraform apply`, `kubectl apply`, `cdk deploy`, `aws * delete-*`, … | deny | ask | ask |
| read secrets (`ccenv secret`, `sops`, `gh auth token`, credential files) | deny | deny | deny |
| `ccenv exec`, `run`, `login`, `sync` | ask | ask | ask |

"Readonly" means read-only toward remote systems and credentials. It is not
plan mode: local edits are allowed. `permissions.builtin: false` turns the
built-in rules off for a profile; your own `deny`/`ask` lists still apply.

Full schema, merge rules and every key: [docs/manifest.md](docs/manifest.md).
A complete example: [examples/environments.yaml](examples/environments.yaml).

## What ccenv supports

### Modules

| Key | What it sets | Notes |
|---|---|---|
| `aws` | `AWS_PROFILE`, `AWS_REGION`, `AWS_DEFAULT_REGION`, optional `AWS_CONFIG_FILE` | `ccenv login` runs `aws sso login`; Claude Code gets `awsAuthRefresh` |
| `gcp` | `CLOUDSDK_*` project, region, configuration and impersonation; `home:` gives an isolated gcloud config | `ccenv login --module gcp [--adc]` |
| `azure` | `ARM_SUBSCRIPTION_ID`, `ARM_TENANT_ID`; `home:` gives an isolated `AZURE_CONFIG_DIR` | Without `home`, `az` keeps using `~/.azure`'s current subscription; only Terraform is pinned |
| `github` | `GH_TOKEN`/`GITHUB_TOKEN` (or the Enterprise variables) and a git credential helper for HTTPS | `auth: "gh:<login>[@host]"` or any secret ref; `home:` gives an isolated `gh` login |
| `git` | name, email, SSH key, signing, per-URL credentials, raw config | Set per process through `GIT_CONFIG_*`; `~/.gitconfig` is never touched |
| `env` | plain variables | Not secret: `sync` may write them to settings files |
| `secret_env` | `VAR: <ref>` | Resolved at launch, into the process environment only |

Everything else (GitLab, Bitbucket, Azure DevOps, npm, uv/pip, cargo, Docker,
kube, Terraform Cloud, Vault, …) is a role built from `env`, `secret_env` and
`git.credentials`: see [docs/recipes.md](docs/recipes.md).

### Tools

| `tool:` | Support |
|---|---|
| `claude` (default) | Providers `subscription`, `gateway`, `api-key`, `bedrock` and `vertex`. A separate login per `claude.home`. Generated settings with permission rules, and a SessionStart note that tells Claude which profile it is in. The gateway token reaches Claude through `apiKeyHelper` and is not exported. |
| `codex` | `codex.home` gives each account its own `CODEX_HOME`. `ccenv login` signs in with ChatGPT, or pipes an API key on stdin. Nothing secret is exported. Permission rules are not rendered for Codex yet. |
| `gemini` | `gemini.home`, an API key or Vertex. `--approval-mode` follows the level: `auto_edit` for power, `default` otherwise. |
| anything else | `ccenv exec <profile> -- <command>`: Cursor, Copilot CLI, Aider, opencode, Goose, or any CLI. |

Codex and Gemini are supported at the environment level in 2.0. Some details
(Codex's environment filter and API-key login, Gemini's Vertex variable) are
not yet verified against the real tools.

### Secret schemes

| Ref | Backend | `ccenv secret set` |
|---|---|---|
| `sops:<group>/<key>` | `~/.config/ccenv/secrets.enc.yaml`, encrypted with [sops](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age), decrypted once per launch | yes (value on stdin, never in argv) |
| `keychain:<service>/<account>` | the macOS Keychain | yes (`security` prompts for the value) |
| `op://vault/item/field` | the 1Password CLI (`op read`) | no, use `op` |
| `gh:<login>[@host]` | the token `gh` stores for that account | no, use `gh auth login` |
| `cmd:["prog", "arg", …]` | any program's stdout, run without a shell (`secret-tool`, `pass`, `bw`, `vault`, `doppler`, …) | no |

Exactly one trailing newline is stripped. A missing secret is a warning: the
variable stays unset and the launch goes ahead.

## Where it works

Claude Code can start in several ways, and they do not all read the same
configuration. ccenv covers each one as far as the app allows. The full guide
is [docs/desktop.md](docs/desktop.md).

| Surface | How | Limits |
|---|---|---|
| Terminal | `ccenv run` (Claude, Codex, Gemini) or `ccenv exec` (anything) | None: everything applies. |
| Bare `claude`, IDE extensions | `ccenv sync` writes `.claude/settings.local.json` at each matching repo's git root (for a matched folder that is not a repo: the folder and each git repo up to two levels below it): non-secret env, provider, `apiKeyHelper`, permission rules and the SessionStart note | No `secret_env` and no `claude.home`. A nested profile inside another profile's repo is skipped (use `ccenv run` there). `confirm` profiles are not synced. |
| VS Code extension login check | `ccenv desktop <p> --vscode` prints `claudeCode.environmentVariables` | Whether `apiKeyHelper` satisfies that check is not yet verified. |
| Claude Desktop (local Code sessions) | Synced settings files, plus `ccenv desktop <p> --print-3p` for the app-wide gateway setup (Developer → Configure Third-Party Inference) | One claude.ai account per app. The provider applies to the whole app, not per project. Desktop ignores `apiKeyHelper` and `ANTHROPIC_*` credentials. Whether it runs the SessionStart hook is not yet verified. |
| Cloud sessions, Remote Control | Subscription profiles only. For AWS, run `ccenv aws-handoff` in your terminal and paste the short-lived credentials | Remote Control is off with a gateway, an API key or a cloud provider (`ccenv list` shows `remote=`). |
| Other GUI apps (Cursor, JetBrains, …) | `ccenv exec <p> -- /Applications/X.app/Contents/MacOS/X`, or paste `ccenv shell <p> --format env` into the app's env setting | Apps started from the Dock or Finder do not inherit your shell. `--format env` has no secrets: for a gateway profile Claude gets the URL without the token (use `run`, or `sync` in the repo). |
| Shells, direnv | `eval "$(ccenv shell)"`, `--format fish`, or `use ccenv` with [contrib/direnvrc](contrib/direnvrc) | direnv gets non-secret values only; `gh` there uses its own keyring login (no token). |

## AI agents

Credentials follow the **session**, not the directory. An agent started by
`ccenv run` stays anchored to that profile, even if it `cd`s elsewhere.

- An agent checks its identity with `ccenv status --json`. `"mismatch": true`
  means the current directory is matched to another profile (a directory
  only the `fallback:` covers is no mismatch).
- An agent can use another profile only through
  `ccenv exec <profile> --reason "…" -- <cmd>`, and only if the session's
  profile lists it in `agents_may_use` at the same or a lower level. `confirm`
  profiles always need a human.
- Every refusal starts with a `ccenv-stop:` line and includes a ready-to-run
  command. You run it yourself in a separate terminal. There is no in-session
  approval and no bypass variable.
- Logins, `sync`, `init`, writing secrets and `shell` with secrets are
  human-only. Runs, execs, logins and refusals are logged to
  `~/.local/state/ccenv/audit.jsonl` (names, never values).

```text
ccenv-stop: code=needs_user reason=confirm profile=personal-power
ccenv: refused: personal-power requires a typed confirmation, which only a human in a terminal can give
ccenv: show the user this ask_user text exactly, then stop:
The agent wants to run `terraform apply` as ccenv profile personal-power (power; aws:personal-poweruser, gh:pat).
Agent's reason: "apply the reviewed plan"
If you agree, run it yourself in a separate terminal (not inside the agent session):
  ccenv exec personal-power -- terraform apply
```

To teach an agent the rules, run `ccenv instructions` and add the output to
your project's `AGENTS.md` (for Claude Code, also add `@AGENTS.md` to
`CLAUDE.md`). [skills/](skills/) has Claude Code skills for checking identity
and for the AWS handoff.

**These guardrails are a seatbelt, not a boundary.** They stop a confused or
prompt-injected agent that follows its instructions. They do not stop code
running as your user that sets out to get around them. The real boundary is
the credentials themselves: the server enforces a read-only PAT or a
ReadOnlyAccess role. [docs/agents.md](docs/agents.md) covers the threat model,
the full gate rules and stronger options. If you are an agent working on this
repository, read [AGENTS.md](AGENTS.md).

## Install

You need Python 3.9 or later; macOS and Linux ship it. Everything else is
optional:

| Tool | Needed for |
|---|---|
| PyYAML or `yq` (mikefarah/yq, or Debian/Ubuntu's `yq` package) | a YAML manifest (otherwise write it as JSON; `python3 -m pip install --user pyyaml` or `brew install yq`) |
| [Claude Code](https://code.claude.com), `codex`, `gemini` | `ccenv run` with that tool |
| `aws`, `gcloud`, `az`, `gh` | that module's logins and probes |
| `sops` + `age` | `sops:` secrets |
| `op` | `op://` secrets |

**install.sh** downloads the single-file release, checks its SHA-256 and
installs it as `~/.local/bin/ccenv`. It never uses sudo, and it refuses to
write through a symlink.

```bash
curl -fsSLO https://raw.githubusercontent.com/TsenkoTsenkov/ccenv/main/install.sh
sh install.sh                     # or: sh install.sh --version v2.0.0
```

The default installs the latest stable release. A prerelease (a tag such as
`v2.0.0b1`) is installed only by name: `sh install.sh --version v2.0.0b1`.

The checksum catches a broken download. To check that the file was built by
this repository's release workflow, run the
`gh attestation verify` command the installer prints at the end.

**uv or pipx**, from git. This install includes PyYAML.

```bash
uv tool install git+https://github.com/TsenkoTsenkov/ccenv
pipx install git+https://github.com/TsenkoTsenkov/ccenv
```

**A git checkout with a symlink.** Update with `git pull`.

```bash
git clone https://github.com/TsenkoTsenkov/ccenv ~/.local/share/ccenv
mkdir -p ~/.local/bin
ln -s ~/.local/share/ccenv/bin/ccenv ~/.local/bin/ccenv    # any directory on your PATH
```

ccenv is a tool, not a library, and it needs only Python, so there are no
npm, cargo or gem packages.

**For `sops:` secrets** (optional; `keychain:`, `op://` and `gh:` refs do not
need it):

```bash
brew install sops age                     # or your package manager
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt && chmod 600 ~/.config/sops/age/keys.txt
ccenv init                                # writes ~/.config/ccenv/.sops.yaml for that key
ccenv secret set sops:client-a/token      # prompted
```

Your configuration lives in `~/.config/ccenv` (override with `CCENV_ROOT`).
It is separate from this repository. Keep it private, or in a private repo of
its own. If you export `CCENV_ROOT` or `CCENV_STATE` in your shell profile
pointing somewhere other than the default, agents without a `ccenv run`
session (bare `claude`, the IDE extension, Desktop) are refused every
`ccenv exec` with `reason=override`, because an agent could set the same
variable to load another manifest. Start those agents with `ccenv run` (its
session record remembers the paths), or set the variables per command
([docs/agents.md](docs/agents.md#the-gate)).

## Commands

`ccenv help` shows the everyday commands; `ccenv help <command>` lists a
command's flags, `ccenv help manifest` and `ccenv help agents` give short
primers, and `ccenv --help` lists everything. `--cwd DIR` works with any command
and resolves the profile as if you were in `DIR`; it wins over the session's
profile in `status`, `exec` and `run`. Arguments after `--` go to
the launched tool: `ccenv run client-a -- --resume`.

**Everyday**

| Command | What it does |
|---|---|
| `ccenv init` | Create `~/.config/ccenv` with a starter manifest (and a `.sops.yaml` when an age key exists). Never overwrites. |
| `ccenv run [profile] [-t claude\|codex\|gemini] [--login] [-- args]` | Launch the profile's tool with its identity. Warns about expired AWS credentials; logs in only with `--login`. |
| `ccenv exec [profile] [--reason TEXT] -- cmd …` | Run any command with a profile's identity. Returns the command's own exit code. |
| `ccenv status [profile] [--why] [--probe] [--json]` | Which profile applies and what it sets. `--why`: the `use:` chain and where each value comes from. `--probe`: check each login. Alias: `whoami`. |
| `ccenv list [--check] [--json]` | All profiles and roles. `--check` also probes AWS and gateway secrets. |
| `ccenv login [profile] [--module M] [--all]` | Log in: AWS SSO by default (plus Codex for Codex profiles). `--module gcp\|azure\|github\|codex` (for a `gh:` account, gh's previously active account is switched back afterwards). `--all`: one AWS login per SSO session or portal. |
| `ccenv sync [profile] [--dry-run] [--clean [--force]]` | Write non-secret settings into each matching repo's `.claude/settings.local.json`, for bare `claude`, IDEs and Desktop. `--clean` also removes what ccenv wrote in files no profile syncs any more (`--force` for files that hide `~/.claude/settings.json`'s provider routing). |
| `ccenv doctor [--strict] [--json] [--fix]` | Check tools, the manifest, file modes, and the shell exports `run` would clear. `--fix` only tightens ccenv's own directories to 0700. |

**Advanced**

| Command | What it does |
|---|---|
| `ccenv shell [profile] [--format sh\|fish\|direnv\|json\|env] [--no-secrets]` | Print the environment, for `eval "$(ccenv shell)"`, direnv or a GUI app. |
| `ccenv secret set REF` / `ccenv secret --ref REF` | Store a secret (prompted, or from stdin), or print one (helpers use this). |
| `ccenv desktop [profile] [--print-3p] [--vscode]` | What reaches Claude Desktop and IDEs, plus blocks to paste there. |
| `ccenv instructions [--agent claude\|codex\|gemini] [--direnv]` | Print the agent rules, or the direnv function. |
| `ccenv migrate [--write]` | Print a v1 manifest as version 2. `--write` replaces it and keeps the old file. |
| `ccenv aws-handoff [profile] [--stdout] [--login]` | Copy short-lived AWS credentials for a cloud session. |

Exit codes: `0` ok, `1` error, `2` usage, `3` needs the user (a `ccenv-stop:`
refusal), `4` credentials expired. Apart from these, `exec` and `run` return
the tool's own exit code.

## Manifest reference

`~/.config/ccenv/environments.yaml` has five top-level keys: `version: 2`,
`profiles`, `roles`, `defaults` (the first layer of every profile) and
`fallback` (the profile used when no directory matches). A profile or role
body takes these keys:

| Key | Meaning |
|---|---|
| `match` | Directories or globs (profiles only, not inherited). The longest match wins. |
| `use` | Roles or profiles to layer underneath, in order. |
| `level`, `confirm` | Privilege level and typed confirmation (see above). |
| `agents_may_use` | Profiles an agent in this profile's session may `exec` (same or lower level). |
| `tool` | `claude` (default), `codex` or `gemini`: what `run` launches. |
| `aws`, `gcp`, `azure`, `github`, `git` | Modules: a map, `null` (off and isolated) or `"inherit"`. |
| `claude`, `codex`, `gemini` | Tool settings: provider, home, models, args, raw Claude `settings`. |
| `env`, `secret_env` | Plain variables, and variables resolved from secret refs. `null` unsets one. |
| `permissions` | `deny`/`ask` lists of Claude rules; `builtin: false` drops the level's rules. |
| `inherit`, `unset` | Variables to keep from your shell, or to always remove. |
| `description` | Shown by `list` and `status`. |

Unknown keys produce a warning with a "did you mean" suggestion.
[docs/manifest.md](docs/manifest.md) is the full reference.

**Files.** Apart from the settings that `sync` writes, ccenv keeps everything
outside your projects:

| Path | Contents |
|---|---|
| `~/.config/ccenv/` (`CCENV_ROOT`) | `environments.yaml`, `secrets.enc.yaml`, `.sops.yaml` |
| `~/.local/state/ccenv/` (`CCENV_STATE`) | per-account homes (`homes/<tool>/<name>`), the sync ledger, session records, `audit.jsonl`. Session records are also kept under your passwd home's `~/.local/state/ccenv/sessions/`, which no variable moves |
| `~/.cache/ccenv/` | generated Claude settings (no secrets, mode 0600, pruned after 7 days) |

## Security

- **Credentials are the boundary; agent rules are a seatbelt.** Give each
  profile the narrowest credentials that work, such as a fine-grained
  read-only PAT or a ReadOnlyAccess role. A separate SSO start URL or MFA for
  power roles makes the split real.
- **No secret is written to disk in clear text.** The manifest holds refs.
  Generated and synced settings hold no secret values. Helpers fetch gateway
  tokens and git credentials when they are used.
- **Secrets in the process environment are visible inside the session.**
  `secret_env`, `GH_TOKEN` and similar values sit in the process environment,
  so the agent and its subprocesses can read them. Use narrow tokens, and for
  Claude Code consider `env: { CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: "1" }`.

Details and how to report a problem: [SECURITY.md](SECURITY.md).

## Upgrading from v1

v1 manifests (no `version`, or `version: 1`) keep working unchanged. A
translator maps them onto the v2 engine and keeps v1 behaviour where it
matters: no built-in agent rules, the gateway token is still exported, and
only the variables v1 already managed are cleared (AWS ones only where the
environment has an `aws:` block, so elsewhere your shell's `AWS_PROFILE`
stays, as in v1).

```bash
ccenv migrate            # print the v2 version and list what would change
ccenv migrate --write    # switch; the old file is kept as environments.v1.yaml
```

`migrate` keeps your existing sops paths (`sops:<env>/<key>`) and never reads
or changes `secrets.enc.yaml`. The step-by-step guide, with rollback, is
[docs/migration.md](docs/migration.md). [CHANGELOG.md](CHANGELOG.md) lists
every behaviour change.

## Development

```bash
python3 -m unittest discover -s tests -v
uv run --no-project --python 3.9 python -m unittest discover -s tests    # oldest supported Python
```

The tests use only the standard library. Each test gets its own temporary
`HOME`, `CCENV_ROOT`, `CCENV_STATE` and XDG directories, and `PATH` points at
fake `gh`, `aws`, `sops`, `security`, `op`, `gcloud`, `az`, `claude`, `codex`
and `gemini` binaries, so no test touches a real login. CI runs on Ubuntu and
macOS with Python 3.10 and 3.13, plus Python 3.9 through uv, and builds the
package. Contributor notes are in
[AGENTS.md](AGENTS.md): the tables-first design, placeholder names only,
Python 3.9 compatibility, and a single source for the agent rules.

## License

[MIT](LICENSE)
