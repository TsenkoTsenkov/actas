# actas and AI agents

This guide is for the human who decides what coding agents may do with their
credentials. It covers levels, permission rules, which profiles an agent may
switch to, what actas refuses and why, and what actas cannot protect against.

The agent's side is short. It lives in [AGENTS.md](../AGENTS.md) and in
`actas instructions`.

> **Credentials are the boundary. Agent rules are a seatbelt.**
> A read-only token, an AWS `ReadOnlyAccess` role or an Azure `Reader`
> assignment is enforced by the server. Everything actas does inside your
> machine (rules, refusals, typed confirmations, the audit log) stops an agent
> that makes a mistake or follows injected instructions. It does not stop
> code that is determined to get around it. See [Threat model](#threat-model).

Contents:
[In five minutes](#in-five-minutes) ·
[Levels and rules](#levels-and-rules) ·
[Customising rules](#customising-rules) ·
[confirm and agents_may_use](#confirm-and-agents_may_use) ·
[The gate](#the-gate) ·
[Refusals and exit codes](#refusals-and-exit-codes) ·
[Audit log](#audit-log) ·
[Threat model](#threat-model) ·
[Per-agent notes](#per-agent-notes) ·
[Scripts and CI](#scripts-and-ci) ·
[Prompts you can paste](#prompts-you-can-paste) ·
[Not yet verified](#not-yet-verified)

---

## In five minutes

1. **Give each profile a level** that matches its credentials: `readonly`,
   `standard` (the default) or `power`.
2. **Put `confirm: true` on anything dangerous.** Agents can never use those
   profiles, and you type the profile name to use them yourself.
3. **List the profiles an agent may hop to** with `agents_may_use` (or
   `["*"]` in `defaults:` to let agents use every profile at the same or a
   lower level). actas refuses every other profile (see [The gate](#the-gate) for sessions not
   started by `actas run`). These checks are guardrails against mistakes, not
   a boundary: the boundary is each profile's credentials (see
   [Threat model](#threat-model)).
4. **Start agents with `actas run`.** This anchors the session, so actas knows
   which profile the agent belongs to.
5. **Tell the agent the rules:** `actas instructions >> AGENTS.md`. For
   Claude Code, add `@AGENTS.md` to `CLAUDE.md`, or install the skill (see
   [Claude Code](#claude-code-cli-started-with-actas-run)).

```yaml
version: 2
roles:
  no-apply:                                   # a permission set is just a role
    permissions: { deny: ["Bash(terraform apply *)", "Bash(terraform destroy *)"] }
  client-a-base:
    git:    { name: Ada Example, email: ada@client-a.example }
    github: { auth: "gh:ada-client-a" }
profiles:
  client-a-test:
    match: ~/code/client-a
    use: [client-a-base]                      # level standard (the default)
    aws: { profile: client-a-test-dev, region: eu-west-1 }
    agents_may_use: [client-a-prod]           # an agent here may run prod read-only commands
  client-a-prod:
    match: ~/code/client-a/infra/prod
    use: [client-a-base, no-apply]
    level: readonly
    github: { auth: "sops:client-a-prod/gh_ro_pat" }   # a read-only PAT makes readonly real
    aws: { profile: client-a-prod-readonly, region: eu-west-1 }
  client-a-admin:
    use: [client-a-base]
    level: power
    confirm: true                             # a human types the name; agents are refused
    aws: { profile: client-a-admin, region: eu-west-1 }
```

The four rules of the manifest apply here too: **later layers win, maps
merge** · **`null` means off: the tool sees no account** · **compose upward:
a profile is at least as privileged as what it uses** · **a secret is always
`scheme:path`**. See [docs/manifest.md](manifest.md).

---

## Levels and rules

A **level** says how much the profile's credentials can do. The effective
level is the highest level anywhere in the profile's `use:` chain, or
`standard` if none is set. A profile that declares a lower level than a layer
it uses is an error ("compose upward").

"Readonly" means read-only toward **remote systems and credentials**. It is
not plan mode: agents may still edit local files at every level.

Each level renders a built-in **preset** of Claude Code permission rules:

| What | Example rules (Claude syntax) | readonly | standard | power |
|---|---|---|---|---|
| Local edits | none | allowed | allowed | allowed |
| Push | `Bash(git push *)`, `Bash(gh pr merge *)`, `Bash(gh release *)`, `Bash(glab mr merge *)` | **deny** | ask | allowed |
| Deploy | `terraform`/`tofu apply` and `destroy`, `kubectl apply`/`delete`, `helm install`/`upgrade`/`uninstall`, `pulumi up`/`destroy`, `cdk deploy`/`destroy`, `sam deploy`, `aws * delete-*`/`put-*`/`create-*`, `gcloud * delete *`, `az * delete *` | **deny** | ask | ask |
| Secrets | `actas secret`/`shell`/`aws-handoff`/`git-credential` (by any path, and as the exact helper command lines actas writes), `git credential`, `sops`, `gh auth token`, `aws configure export-credentials`, `security find-generic-password`, `op read`; `Edit` of the actas config and state dirs; `Read` of the age key and of credential files (below) | **deny** | **deny** | **deny** |
| Switch | `actas exec`, `actas run`, `actas login`, `actas sync` | ask | ask | ask |

- **allowed** means actas adds no rule, so Claude's own permission mode
  decides. actas never adds allow rules.
- **The credential-file rules** are built from your real paths. They cover
  `Edit(<config dir>/**)`, `Edit(<state dir>/**)` and `Read(<age key>)`. They
  add `Read` rules only for the modules your manifest uses (a directory is
  denied both as itself and as `<dir>/**`):
  - `~/.aws/sso/cache`, `~/.aws/cli/cache` and `~/.aws/credentials`
  - `~/.config/gcloud` and `~/.azure`
  - `~/.config/gh/hosts.yml`
  - `~/.claude/.credentials.json` and `~/.codex/auth.json`
  - `~/.gemini/oauth_creds.json`, `~/.gemini/mcp-oauth-tokens.json` and
    `~/.gemini/.env`
  - the matching files inside actas-managed homes and `home:` paths. In a
    Claude home only `.credentials.json` is denied, so the agent can still
    read its own memory and skills there; a Gemini home keeps its
    `.gemini/settings.json` readable.
- **Approving the "Switch" ask** only lets `actas exec` start. actas's own
  [gate](#the-gate) still decides whether the command runs.
- **Where rules go:** into the settings file that `actas run` passes to
  Claude Code (`--settings`), and into `.claude/settings.local.json` written
  by `actas sync`.
- **v1 manifests** (no `version: 2`) render no rules until you run
  `actas migrate`.
- **Other tools:**
  - **Gemini CLI:** actas passes `--approval-mode default` for readonly and
    standard, and `auto_edit` for power.
  - **Codex:** no rules are rendered in this release. Use Codex's own sandbox
    and approval settings.

See what a profile renders with `actas status <profile> --why` (a count) or
`actas status <profile> --why --json` (the full `agent_policy`).

---

## Customising rules

`permissions` takes three keys:

| Key | Type | Effect |
|---|---|---|
| `deny`, `ask` | lists of Claude rules | Added to the preset. Lists from every layer are **unioned and sticky**: a later layer cannot remove them, and `null` over them is an error naming the layer that added them. |
| `builtin` | bool, default `true` | `false` drops the level's preset and renders only your own lists. `doctor` reports which layer turned it off. |

A **permission set is just a role** that carries only `permissions` and/or
`level`. Compose several with `use:`:

```yaml
roles:
  no-pulumi: { permissions: { deny: ["Bash(pulumi up *)", "Bash(pulumi destroy *)"] } }
  no-apply:  { permissions: { deny: ["Bash(terraform apply *)"] } }
profiles:
  personal:
    match: ~/code/personal
    use: [no-pulumi, no-apply]               # both deny lists, de-duplicated
    permissions: { ask: ["Bash(npm publish *)"] }
```

`claude.settings` is a raw block, merged last into Claude's settings. Its
`permissions` lists are added to the rules too.
`claude.settings.permissions.defaultMode: plan` works. `auto` and
`bypassPermissions` are rejected.

---

## confirm and agents_may_use

| Key | Where | Meaning |
|---|---|---|
| `confirm: true` | any profile or role, not `defaults` (OR'd through `use:`) | A human must type the profile name to use it. Agents are **always** refused, even when it is their own session profile. `actas sync` skips these profiles. |
| `agents_may_use: [names]` or `["*"]` | profiles and `defaults:` (a profile's own list replaces the default); not inherited through `use:` | Profiles an agent in a session of this profile may `actas exec` without you (`"*"`: any profile), if they are at the **same or a lower level** and have no `confirm`. Hops do not chain: when an agent starts a nested `actas run T`, the new session keeps the outer session's list (stored in its record as `allowed`), not T's. |

Hops sideways also need `agents_may_use`. Without it, an agent in
`client-a-test` could reach `client-b`, which is also `standard`.

If you would rather let agents push and deploy as any of your everyday
profiles, and keep the gate only for higher levels and `confirm`, allow them
all:

```yaml
defaults:
  agents_may_use: ["*"]
```

A v1 manifest accepts the same key in `defaults:` or in an environment. The
`not_allowed` refusal says where to add a profile. A nested `actas run`
session keeps the list it started with, so start a new session after you
change it there.

`doctor` flags:

- `agents_may_use` targets that will always be refused (a higher level, or
  `confirm`);
- `level: power` without `confirm`;
- `readonly` profiles that use a `gh:` account. A gh OAuth token usually has
  full repo scope, so use a fine-grained read-only PAT ref instead.

**"Always read-only, power on demand":** keep the power role out of the
everyday profile. Make a separate profile with `confirm: true`, and use it
only when you need it: `actas exec personal-power -- <cmd>`. The full recipe
is [read-only by default, power on demand](recipes.md#read-only-by-default-power-on-demand).

**"Let agents push as any of my accounts":** `defaults: { agents_may_use: ["*"] }`.
See [agents that push as any of your accounts](recipes.md#agents-that-push-as-any-of-your-accounts).

---

## The gate

`actas run` and `actas exec` decide every call with the tables below. Three
terms first:

- **Agent context:** any of these signals is present.
  - Env markers: `ACTAS_AGENT`, `CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`,
    `CLAUDE_CODE_CHILD_SESSION`, `GEMINI_CLI`, or any `CODEX_SANDBOX*`.
  - An ancestor process named `claude`, `codex`, `gemini`, `agy`,
    `cursor-agent`, `copilot`, `aider`, `opencode`, `goose`, `amp` or
    `kiro-cli`, or one whose path contains `Claude.app/`.

  A non-interactive shell with `ACTAS` set (direnv, Makefiles, git hooks) is
  **not** a signal. `actas status` shows the signals it found.
- **Session profile (S):**
  - If `actas run` started the agent, S comes from the **session record** it
    wrote (`<state>/sessions/<pid>.json`, plus a copy under your passwd
    home's `~/.local/state/actas/sessions/`). actas checks the record against
    the process's start time, so a reused pid does not inherit it. The
    session is *anchored*.
  - Otherwise S comes from `$ACTAS`, if it names a profile. That is
    self-asserted: synced settings, an IDE, Desktop, or a tool started with
    `actas exec`. When actas cannot read the process table at all, it does
    not trust `$ACTAS` for agents (S is none).
  - **S never comes from the current directory.**
- **Target (T):** the profile named on the command line, or the profile of
  an explicit `--cwd DIR`. Without either, an agent's default is S, or the
  cwd's profile when there is no S (which then runs only if it is
  readonly), and a human's default is the profile matching the current
  directory. An explicit target is always gated; it never silently falls
  back to S.

**Agent context.** The rows are checked in order and the first match wins.
Nothing in this branch ever prompts.

| # | Situation | Result |
|---|---|---|
| 1 | T has `confirm` | refused: `confirm` |
| 2 | Anchored, but the config or state directory differs from the one the record holds (`ACTAS_ROOT`, `ACTAS_STATE`, `XDG_STATE_HOME`, `HOME`) | refused: `override` |
| 3 | Not anchored, and `ACTAS_ROOT` or `ACTAS_STATE` names a place other than the default, or `ACTAS_SESSION` is set (`actas run` started this agent, but its record is not found) | refused: `override` |
| 4 | No session at all (no record, no `$ACTAS` naming a profile) | T is the **readonly** profile that the agent's own working directory resolves to (not `--cwd`): **allowed**; anything else, including other readonly profiles: refused: `no_session` |
| 5 | T is neither S nor in S's `agents_may_use` (or, in a nested `actas run`, not in the outer session's list) | refused: `not_allowed` |
| 6 | T's level is higher than S's | refused: `elevation` |
| 7 | Not anchored, and T is `power` | refused: `unanchored` |
| 8 | Otherwise | **allowed** |

The pre-rename names (actas was called ccenv) count exactly like the new ones:
`CCENV_ROOT` and `CCENV_STATE` in rows 2 and 3, `CCENV_SESSION` in row 3, and
`$CCENV` (read when `$ACTAS` is unset) in row 4. Records an old `ccenv run`
wrote are found while the state directory is still `~/.local/state/ccenv`;
after it moved, such a record names the old directory and row 2 refuses.

**Human context:**

| Situation | Result |
|---|---|
| T has `confirm`, or `$ACTAS` names a profile with a lower level than T | actas asks you to type T's name on the terminal (`/dev/tty`) and shows the command. The right name runs it. A wrong name exits 1. No terminal: refused with `no_tty`. |
| Otherwise | allowed |

What this means in practice:

- Setting `ACTAS=<power profile>` inside an `actas run` session changes
  nothing: the record wins. Moving `ACTAS_STATE`, `XDG_STATE_HOME` or `HOME`
  does not lose the record either: actas looks it up under your passwd home
  (from the user database, not `$HOME`) first, finds that the state directory
  changed, and refuses with `override`, even when `ACTAS_SESSION` was
  removed. A record written into a moved state directory does not beat that
  copy. Code that forges or deletes the copy gets around this; that is the
  "determined code" case in the [threat model](#threat-model).
- If you export `ACTAS_ROOT` or `ACTAS_STATE` globally to a non-default
  place, row 3 refuses every agent that was not started with `actas run`.
  Start agents with `actas run` (its record stores the paths), or set the
  variables per command.
- `cd` into another profile's directory changes nothing either.
- Without an anchor (bare `claude`, IDE, Desktop), S is self-asserted. An
  agent that ignores its rules could change `ACTAS` to another profile that is
  not `power` and has no `confirm`, or `cd` into another readonly profile's
  directory. These rules catch mistakes; they are not a boundary. That is why
  such sessions never reach power profiles. Start agents with `actas run`
  when the difference matters.
- There is no bypass flag or variable. Anything an agent can set, such as an
  imagined `ACTAS_HUMAN=1`, could not prove a human is there.

**Human-only commands** refuse in agent context with `reason=human_only`:

- `login`, `sync` (including `--dry-run`), `init`, `aws-handoff`
- `secret set REF` and `secret ENV KEY --set`
- `migrate --write`
- `shell` with secrets

`shell --no-secrets`, `shell --format json` and `shell --format env` print no
secrets, and actas allows them in agent context. Claude Code sessions with
actas rules still deny every `actas shell` call, and rule 7 tells agents not
to use it: these forms are meant for direnv and GUI-app setup.

**`!` commands count as agent context.** A command you type with `!` inside
Claude Code runs in Claude's Bash tool, with `CLAUDECODE` set. Run elevated
commands in a separate terminal tab.

---

## Refusals and exit codes

Every refusal prints this as the **first** line on stderr:

```text
actas-stop: code=needs_user reason=<reason> profile=<target>
```

Then comes a sentence, then the `ask_user` text, which the agent must show
you word for word:

```text
The agent wants to run `terraform apply` as actas profile client-a-admin (power; aws:client-a-admin, gh:ada-client-a).
Agent's reason: "apply the reviewed plan"
If you agree, run it yourself in a separate terminal (not inside the agent session):
  actas exec client-a-admin -- terraform apply
```

A `not_allowed` refusal (when allowing the hop would work) ends with one more
line, addressed to you rather than the agent:

```text
For the user, not the agent: to let agents in personal sessions do this, add work (or "*" for every profile at the same or a lower level) to agents_may_use in defaults (applies to every profile without its own list), or give profiles.personal its own list (it then replaces the defaults one), in ~/.config/actas/environments.yaml.
```

It names the profile's own list when it has one, and in a nested `actas run`
it says to start a new session after the change.

How `ask_user` is built:

- The command comes from argv and is shown with shell quoting, so a `;` or a
  `|` inside an argument is displayed, never run.
- If the command starts a shell, an interpreter, a package runner or a
  wrapper (`sh`, `bash`, `python`, `node`, `deno`, `npx`, `uvx`, `bunx`,
  `make`, `awk`, `env`, `xargs`, `sudo`, `nohup`, `ssh`…), runs code through
  a subcommand (`pipx run`, `uv run`, `go run`, `cargo run`, `npm exec`,
  `docker run`/`exec`, `kubectl exec`/`run`/`debug`…) or contains
  `; | & $`, a backtick or a newline, a line is added: `Warning: this runs a
  shell or interpreter; read the whole command before running it.`
- **Credential values are hidden.** When the agent's shell expanded a
  credential into the command (`curl -H "Authorization: Bearer $GH_TOKEN"`),
  the value of that variable is shown as `<$GH_TOKEN>`, and a token-shaped
  string (`ghp_…`, `sk-…`, `AKIA…`, a JWT, a `Bearer` value) as `<token>`,
  in the text, in the suggested command and in the `--json` `command`.
  Hidden are values (8+ characters) of known credential variables
  (`GH_TOKEN`, `ANTHROPIC_API_KEY`, `AWS_SECRET_ACCESS_KEY`…), of every
  `secret_env` name in the manifest, and of any variable whose name contains
  `TOKEN`, `SECRET`, `PASSW`, `API_KEY` or `ACCESS_KEY`. A `Note:` line then says the
  command does not run as shown: put the value back yourself, preferably
  the target profile's own (`sh -c '… "$GH_TOKEN" …'`), since the agent's
  copy belongs to its session. The audit log's `argv_sha256` still hashes
  the real argv; `cmd0` is hidden the same way.
- The agent's `--reason` loses control characters and is capped at 200
  characters. **Read the command, not the reason.**

With `--json`, the refusal is also printed on stdout:
`{"ok": false, "code", "reason", "current", "requested", "command",
"agent_reason", "ask_user", "warnings"}`.

| Reason | Meaning |
|---|---|
| `confirm` | The target needs a typed confirmation, which only a human in a terminal can give |
| `override` | The config or state directory differs from the session's; without an anchor, `ACTAS_ROOT`/`ACTAS_STATE` name a non-default place, or `ACTAS_SESSION` is set but its record is not found |
| `no_session` | The agent has no actas session; only the readonly profile of its working directory runs |
| `not_allowed` | The target is neither the session's profile nor in its `agents_may_use` (the text says where to add it; see [Agents that push as any of your accounts](recipes.md#agents-that-push-as-any-of-your-accounts)) |
| `elevation` | The target's level is higher than the session's |
| `unanchored` | A power target, and the session was not started by `actas run` |
| `no_tty` | A human-context confirmation with no terminal to ask on |
| `human_only` | A human-only command |
| `login` | Expired credentials (with `code=expired`) |

| Exit | Meaning |
|---|---|
| 0 | ok |
| 1 | error (including a missing secret or a declined confirmation) |
| 2 | usage |
| 3 | needs the user (`actas-stop: code=needs_user`) |
| 4 | credentials expired, login needed (`actas-stop: code=expired`); from `status --probe`, `login` and `aws-handoff` |
| 130 | interrupted |

`actas exec` and `actas run` return **the command's own exit code**
(`terraform plan -detailed-exitcode` returns 2, many CLIs return 3 to 5). So
agents must key on the `actas-stop:` line, never on the number.

---

## Audit log

actas appends one JSON line per event to `audit.jsonl` in its state directory:
`$ACTAS_STATE`, else `${XDG_STATE_HOME:-~/.local/state}/actas`. The file is
mode 0600.

```sh
tail -n 50 ~/.local/state/actas/audit.jsonl
```

- **Events:**
  - `run`, `exec`, `login`, `aws-handoff`, `sync` and `secret-set`.
  - Every refusal, and every declined confirmation.
  - `secret-read` and `git-credential`, but only when an agent is detected.
- **Fields:**
  - `ts`, `action`, `profile`, `level`, `from_profile`.
  - `decision`: `allowed`, `confirmed-tty`, `refused-needs-user` or
    `refused-confirm`.
  - `reason`, and `agent` (`detected`, `signals`, `session`, `record_pid`).
  - `cmd0` (the program name, with a credential value hidden as in
    `ask_user`) and `argv_sha256` (a hash of the full command).
  - `cwd` (with `~`), `refs` (names only) and `actas_version`.
- **Never secret values.** The log is a record of what happened, not a
  defence: same-user code can edit it.

---

## Threat model

**What actas stops:** an agent that follows its instructions but is wrong,
confused or prompt-injected, or one that retries naively:

- It cannot quietly pick up another client's credentials just by changing
  directory.
- It cannot quietly escalate to a power profile.
- It cannot log in, sync settings, set secrets or print a profile's
  environment with secrets (`actas shell`) through actas.
- It is told how to hand the decision to you.

**Secret reads through the helpers are not refused.** `actas secret ENV KEY`,
`actas secret --ref <ref>` and `actas git-credential` are the helpers that
Claude's `apiKeyHelper` and git call, so they print any ref's value in agent
context too. They are audit-logged (`secret-read`), and
[rule 7](../AGENTS.md#credentials-actas) forbids `actas secret` and
`git credential`. For Claude Code, v2 manifests also render Bash deny rules
for all three helpers unless `permissions.builtin: false`: for the bare
`actas`/`ccenv` name, for any path ending in `/actas` or `/ccenv` (so
`python3 /…/bin/actas secret …` too), and for the exact command lines actas
writes into `apiKeyHelper` and `GIT_CONFIG_VALUE_*`. Wrapped forms
(`sh -c`, `eval`) are not covered; Bash rules are not a boundary. v1
manifests, Codex, Gemini and other `exec`-launched agents have no such rule.

**What actas does not stop:** code running as your user that is determined to
get around it. Such code can:

- call `aws --profile <anything>` directly, and use any `gh` account in the
  keyring;
- run `git credential fill`, read the age key or the AWS SSO cache, and forge
  actas's state files or audit log;
- start a process outside the agent's process tree (a launchd job, `nohup`
  through another app), which actas cannot tell from a human's (actas runs
  `/bin/ps` or `/usr/bin/ps`, so a fake `ps` on `PATH` does not hide the
  ancestry);
- forge or delete session records, including the copy under the passwd
  home;
- outside an `actas run` session, set `ACTAS` or `cd` to another profile's
  directory (self-asserted `$ACTAS` and the readonly-profile-of-the-directory
  rule are guardrails against mistakes).

The AWS SSO cache can mint every role your SSO portal grants (not yet
verified for every setup). Claude's `Read` deny rules cover Claude's file
tools, but `cat` through Bash is a different path. Bash deny rules are, in
Claude Code's own words, not a security boundary.

**Secrets in the process environment:** some values have to be in the
agent's environment, where it can read them with `env`:

- `secret_env` values
- GitHub tokens from `github.auth` (`GH_TOKEN`, `GITHUB_TOKEN`)
- `claude.oauth_token`, `claude.bearer_token`, and the gateway token when
  `claude.export_token: true`
- `gemini.api_key`

By contrast, the gateway token behind `apiKeyHelper` is never exported in v2,
and Codex logins stay in Codex's own home. For Claude Code profiles that carry
process-env secrets, consider `env: { CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: "1" }`.
According to Claude Code's docs it strips credentials from Bash, hook and MCP
subprocesses, but leaves GitHub tokens.

**Real boundaries, strongest last:**

1. **Narrow credentials:**
   - read-only fine-grained GitHub PATs for readonly profiles
   - AWS `ReadOnlyAccess`, GCP viewer and Azure Reader for readonly profiles
   - short-lived tokens wherever possible
2. **Make power need a human outside the agent** (see
   [read-only by default, power on demand](recipes.md#read-only-by-default-power-on-demand)
   and the [hardening checklist](../SECURITY.md#hardening-checklist)):
   - a separate AWS SSO start URL, or `mfa_serial`, for elevated roles
   - Azure PIM activation
   - power-profile secrets encrypted only to an `age-plugin-se` (Touch ID)
     identity
3. **Isolate the agent:** a [devcontainer or VM](recipes.md#stronger-isolation-devcontainer-or-vm)
   that holds only the credentials it needs. actas has no built-in sandbox
   mode.

---

## Per-agent notes

| Surface | Rules rendered | Session anchored | How to give it the agent rules |
|---|---|---|---|
| Claude Code CLI via `actas run` | yes (`--settings`) | yes | `AGENTS.md` + `@AGENTS.md` in `CLAUDE.md`, or the skill; a SessionStart note is added automatically |
| Bare `claude` or the IDE extension in a synced repo | yes (`settings.local.json`) | no (`$ACTAS` from synced settings) | same |
| Claude Desktop, local Code sessions | yes (same settings files) | no | same |
| Claude Code cloud sessions | no | no | no actas; [rule 8](../AGENTS.md#credentials-actas) and `/aws-handoff` |
| Remote Control | as the local session it drives (`actas run`: `--settings`; synced: `settings.local.json`) | as that session | same as that session; subscription profiles only |
| Codex via `actas run -t codex` | no (use Codex's own settings) | yes | `AGENTS.md` (Codex reads it natively) |
| Gemini CLI via `actas run -t gemini` | `--approval-mode` only | yes | `AGENTS.md` with `context.fileName` |
| Cursor, Copilot CLI, Aider, opencode, Goose, Amp, Kiro… via `actas exec` | no | no (`$ACTAS` set by exec) | `AGENTS.md` (most read it natively) |

### Claude Code CLI started with `actas run`

The full experience:

- generated settings (provider, env, permission rules, a SessionStart hook);
- a session record, so the session is anchored;
- `ACTAS_SESSION` in the process env;
- a context note at session start, naming the profile and the rules in one
  line.

To give Claude the full rules:

- `actas instructions >> AGENTS.md`, plus `@AGENTS.md` in `CLAUDE.md`. Only
  the rules go to stdout; the placement hint goes to stderr. Or:
- copy `skills/actas` to `~/.claude/skills/actas`. The skill pre-allows only
  `actas status` and `actas list`. `actas exec` is deliberately left out, so
  Claude asks you first.

### Bare `claude`, the IDE extension and Claude Desktop

These read `.claude/settings.local.json` written by `actas sync`. That file
carries non-secret env, the provider, the permission rules and the
SessionStart note. It never carries `secret_env` values.

- Agents there have no session record. S comes from the `ACTAS` value in the
  synced settings, so power targets and `ACTAS_ROOT`/`ACTAS_STATE` overrides
  (values other than the default places) are refused.
- Desktop is detected by a `Claude.app` ancestor process (not yet verified
  for every Desktop version). Whether Desktop runs project SessionStart hooks
  is not yet verified.
- [docs/desktop.md](desktop.md) explains what reaches each surface.

### Cloud sessions

Cloud sessions have no access to your machine. When one needs AWS:

1. Run `actas aws-handoff <profile>` in your own terminal. It is human-only,
   never logs in on its own, and exits 4 when your SSO session has expired.
   Add `--login` to log in first.
2. Paste the block into the session.

To have the handoff in cloud sessions, commit `skills/aws-handoff` to the
project as `.claude/skills/aws-handoff/SKILL.md`. It is user-invoked: type
`/aws-handoff [profile]` in the session and it gives a scripted handoff.

Remote Control is a local session driven from claude.ai: start it with
`actas run` or in a synced repo, and the notes for those surfaces above
apply ([docs/desktop.md](desktop.md#cloud-sessions-and-remote-control)).

### Codex

- Run it with `actas run -t codex`, or set `tool: codex` in the profile.
- `codex.home` gives each account its own `CODEX_HOME`. `actas login`
  signs Codex in once per home. For `login: api` it pipes the key on stdin.
- actas exports nothing secret for Codex itself.
- No permission rules are rendered in this release.
- Whether Codex passes variables named `*_TOKEN`/`*_KEY`/`*_SECRET` to the
  commands it runs depends on its `shell_environment_policy`. The default is
  not yet verified, and sources disagree.
  - If it strips them, `GH_TOKEN` is missing. The git credential helper
    still works, because it resolves the token when git asks for it, and `gh`
    fails closed (it reports "not logged in" instead of falling back to your
    keyring; this too is not yet verified against real `gh`).
- Detection: a `codex` ancestor process and `CODEX_SANDBOX*` variables (the
  variables are not yet verified).

### Gemini CLI

- Run it with `actas run -t gemini`. The approval mode follows the level.
- Gemini CLI's consumer Google login may not be available for your account
  (not yet verified); use `gemini: { auth: vertex }` with the `gcp` module,
  or `gemini.api_key`.
  The API key lands in the process env.
- Gemini CLI reads `GEMINI.md` by default. To make it read the rules, set
  `context.fileName: ["GEMINI.md", "AGENTS.md"]` in `.gemini/settings.json`
  (`actas instructions --agent gemini` reminds you).
- Gemini loads `.env` files from the project and its parent directories,
  which can override what actas sets. When the manifest uses Gemini,
  `actas doctor` warns about `.env` and `.gemini/.env` files in the current
  directory and its parents (up to your home).
- The `GEMINI_CLI` detection marker is not yet verified.

### Cursor, Copilot CLI, Aider, opencode, Goose, Amp, Kiro and others

- Launch them with `actas exec <profile> -- <agent>`. They inherit the
  profile's environment, and `ACTAS` names the profile. When the command's
  name is one of the agent names under [The gate](#the-gate) (`aider`,
  `copilot`, `amp`, `cursor-agent`…), `exec` also sets `ACTAS_AGENT` to it, so
  everything the agent runs is agent context.
- Without that, detection relies on the ancestor process names. On macOS
  `ps` shows the interpreter for script-based CLIs: Aider runs as `python`
  or `python3`, and npm-installed Copilot CLI, Amp and the `cursor-agent`
  wrapper run as `node`. They are **not** recognised by name when you start
  them without `actas exec`, and neither is Cursor's CLI under its newer
  name `agent`. Mark only the agent's launch, never the profile:
  `actas exec <profile> -- env ACTAS_AGENT=cursor agent`, or a shell alias
  such as `alias aider='ACTAS_AGENT=aider aider'`.
- Do not put `ACTAS_AGENT` in a profile's `env:`. `actas shell`, direnv and
  `sync` would export it into your own terminal, which then counts as an
  agent: `login`, `sync` and `secret set` refuse with `human_only`, and an
  elevation is refused instead of asking you to type the name.
- No rules are rendered for these tools. Use each tool's own permission
  settings.
- GUI apps started from the Dock or Finder do not inherit a shell's env.
  See [docs/desktop.md](desktop.md) and [docs/recipes.md](recipes.md).

---

## Scripts and CI

- **In scripts on your machine,** name the profile explicitly:
  `actas exec client-a-test -- make deploy-plan`. A script you start yourself
  is human context.
  - A script started by an agent is agent context, and it gets the agent
    rules.
  - A `confirm` profile in a script without a terminal is refused (`no_tty`,
    exit 3).
- **In CI,** prefer the platform's own identity: GitHub Actions OIDC to AWS,
  GCP or Azure, and the job's `GITHUB_TOKEN`. actas is built for developer
  machines with several identities.
- **No bypass variable.** There is no `ACTAS_HUMAN=1`, and there never will
  be: an agent could set it as easily as you.

---

## Prompts you can paste

To set the ground rules at the start of a session:

> Before any cloud, GitHub, registry or deploy command, run
> `actas status --json` and follow the "Credentials (actas)" rules in
> AGENTS.md. If actas prints an `actas-stop:` line, show me the `ask_user`
> text and stop.

To allow a read-only hop for one task:

> You may run read-only commands against client-a-prod with
> `actas exec client-a-prod --reason "<why>" -- <command>`. Do not try
> `apply`, `push` or `deploy`; prepare them and tell me what to run.

After a refusal:

> I ran it in my terminal; here is the output: … Continue from there.

---

## Not yet verified

These are implemented with a safe default. They have not been checked against
the real tools, so treat them with care:

- Claude Desktop:
  - whether it runs project SessionStart hooks;
  - whether it passes the settings' `GIT_CONFIG_*` to Bash;
  - the process name of its helper processes.
- Whether the permission lists of a `--settings` file merge with a project's
  own lists.
- Codex and Gemini:
  - their agent env markers;
  - Codex's default env filtering, and Gemini's env redaction.
- `gh` with an empty `GH_CONFIG_DIR` and no token reporting "not logged in"
  (fail closed).
- The Linux `ps` output format (only CI covers it).
- Rule paths that contain spaces.
- How far the AWS SSO cache reaches across roles on one portal.
