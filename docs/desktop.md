# Desktop, IDEs, cloud sessions and other agents

actas applies a profile to **one process**. In a terminal that is easy: `actas run`
or `actas exec` starts the process with the right environment. Apps you start
from the Dock, Finder, a desktop launcher or an IDE never pass through actas, so they get
only what actas can put **in a file they read** (`actas sync`) or what you
**paste into the app** (`actas desktop`, `actas shell --format env`).

This page says exactly what reaches each surface, how to set each one up, and
where the limits are.

> **The rule of thumb.** Environment variables and secrets reach terminal
> sessions. Non-secret settings reach anything that reads
> `.claude/settings.local.json`. App-wide settings (Claude Desktop's gateway,
> the app's signed-in account) actas only prints for you to paste: it never
> changes global app configuration.

Items marked **(not yet verified)** follow the documented behaviour of the
tool but have not been tested hands-on with actas yet. The full list is at
the [end of this page](#not-yet-verified).

---

## At a glance

| Surface | How to set it up | What it gets | What it does not get |
|---|---|---|---|
| **Terminal: `actas run`** | nothing extra | everything: provider, cloud and GitHub identity, secrets, agent rules, session context | — |
| **Terminal: `actas exec <p> -- cmd`** | nothing extra | the profile's full environment (secrets included) for any command | Claude settings (no `--settings` file): for a gateway profile `claude` gets the URL but no `apiKeyHelper`; use `run` for Claude |
| **Bare `claude` in a project** | `actas sync` | provider routing (subscription blanks, or the gateway `apiKeyHelper`), non-secret env (AWS, gcloud, Azure, `GH_CONFIG_DIR`, git identity and credential helper), agent rules, SessionStart note | `secret_env` values; GitHub tokens; a second claude.ai login (`claude.home`); `confirm` profiles |
| **Claude Code in the Claude Desktop app** (local sessions) | `actas sync`, plus `actas desktop --print-3p` once for a gateway | the synced file: agent rules, non-secret env (unless Desktop's own environment already sets that variable) | `apiKeyHelper` and `ANTHROPIC_*` credentials; per-project gateway or account |
| **VS Code / JetBrains extensions** | `actas sync`; for VS Code also `actas desktop --vscode` | as bare `claude` | as bare `claude`; the VS Code login check for gateways needs extra setup |
| **Cloud sessions** (claude.ai/code, mobile) | `actas aws-handoff` for AWS | your claude.ai subscription | local files, gateways, API keys, Bedrock/Vertex |
| **Remote Control** (a local session driven from claude.ai) | `actas run` or `actas sync` as usual | subscription profiles | gateway, API-key, Bedrock and Vertex profiles |
| **Codex CLI** | `actas run -t codex` (or `tool: codex`) | `CODEX_HOME` per profile, cloud/git identity, its own login | actas does not write `config.toml` in 2.0 |
| **Codex app / IDE extension** | launch from an actas environment | whatever environment the app was launched with | nothing from actas when started from the Dock |
| **Gemini CLI** | `actas run -t gemini` | API key or Vertex, `GEMINI_CLI_HOME`, approval mode from the level | — |
| **Cursor and other GUI apps** | `actas exec <p> -- <app binary>`, direnv, or paste `actas shell <p> --format env` | the env you give the app at launch | per-project identity inside one running app |

`actas desktop [profile]` prints a short version of this for one profile. `actas list`
shows a `remote=` column (Remote Control availability) for every profile.

---

## The terminal

```bash
actas run                     # Claude Code in the profile that matches this directory
actas run client-a            # a profile by name
actas run -t codex            # Codex (or gemini) instead of the profile's tool
actas exec client-a -- terraform plan     # any command
```

`actas run` for Claude Code:

- builds the environment: clears the shell's variables for modules the
  manifest uses (it prints `cleared from your shell: …`), sets the profile's
  values, and resolves secrets into the process environment;
- writes a generated settings file (mode 0600, in `~/.cache/actas/`) and starts
  `claude --settings <file>`. A settings-file value beats an exported variable,
  so a stale `export AWS_PROFILE=…` cannot win;
- for a gateway, passes the token through `apiKeyHelper`
  (`actas secret --ref <ref>`), so the token is not in the process
  environment (manifests still on version 1 keep exporting it, as before);
- writes a session record, which lets actas recognise this session's agent
  later (see [docs/agents.md](agents.md)).

This is the only surface where **everything** applies, including
`claude.home` (a second claude.ai subscription) and secrets. When in doubt,
run `actas run` in a terminal, including the terminal inside your IDE.

---

## Bare `claude` and project files: `actas sync`

```bash
actas sync --dry-run      # key names that would change; writes nothing
actas sync                # write every profile's directories
actas sync client-a       # one profile
actas sync --clean        # also remove what actas wrote where no profile syncs any more
actas sync --clean --all  # remove what actas wrote in every synced file (before uninstalling)
```

`sync` writes the profile's **non-secret** settings into
`<repo root>/.claude/settings.local.json` for each directory a profile
matches (the directory itself when it is not inside git). It is human-only: inside an agent session it refuses.

What goes into the file:

| Written | Never written |
|---|---|
| `env`: `AWS_PROFILE`/`AWS_REGION`, gcloud and Azure variables, `GH_CONFIG_DIR`, `GIT_CONFIG_*` (git identity, signing, the credential helper), your `env:` values, `ACTAS`, `ACTAS_LEVEL`, non-interactive variables (`GH_PROMPT_DISABLED=1`, `GIT_TERMINAL_PROMPT=0`, `AWS_PAGER=`) | secret values (`secret_env`, GitHub tokens, API keys) |
| provider blanks: `ANTHROPIC_BASE_URL` and `CLAUDE_CODE_USE_*` set to `""` (or the gateway URL) | `CLAUDE_CONFIG_DIR` (project files ignore it) |
| `apiKeyHelper` (gateway, api-key) and `awsAuthRefresh`, as absolute commands | `CLAUDE_CODE_OAUTH_TOKEN` |
| `permissions.deny` / `permissions.ask` from the profile's level | `ACTAS_SESSION` |
| a SessionStart hook that prints a short note about the profile | |

How sync decides where to write:

- **Longest match wins**, as at run time. A directory is written only for the
  profile that `actas status` would pick there.
- **One file per git repository.** Claude Code reads `settings.local.json` at
  the repository root (a worktree maps to its main checkout). If a nested
  directory belongs to a different profile than its repo root
  (`client-a/infra/prod` inside `client-a`), sync skips it with a warning:
  use `actas run` there. The SessionStart note tells the agent that
  subdirectories may belong to other profiles. A file actas (or v1) wrote
  earlier in such a directory, or in a worktree, is kept current, since
  Claude may still read it there.
- **A folder of repos is covered repo by repo.** When a matched directory is
  not a git repo (`match: ~/code/client-a` holding `api/` and `web/`), sync
  writes the folder itself and each git repo up to two levels below it that
  resolves to the same profile: Claude started in `api/` reads
  `api/.claude/settings.local.json`, never the folder's. Repos deeper down,
  or created after the sync, need another `actas sync` or a `match:` of
  their own.
- **`confirm` profiles are skipped** (`--include-confirm` writes them anyway).
  A profile that needs a typed confirmation should not be reachable without one.
- **Your own keys are kept.** A ledger in `~/.local/state/actas/sync-ledger.json`
  records which keys actas wrote, so a later sync or `--clean` removes only
  those. When the profile sets a key you had set yourself (for example
  `model` through `claude.settings`, or an `env` variable), the ledger keeps
  your value and puts it back when the profile stops setting it or on
  `--clean`. For an env name that looks like a credential (`TOKEN`, `KEY`,
  `SECRET`, `PASS`, `CREDENTIAL`, `AUTH`) it keeps only that the key existed,
  never the value: sync tells you the original was overwritten. Files
  written by ccenv v1 are adopted.
- **`--clean` touches only stale files:** those in the ledger that no profile
  syncs any more (sync lists them as `stale`); a profile that fails to load
  keeps its files. It keeps a file whose removal
  would let `~/.claude/settings.json`'s `apiKeyHelper` or
  `env.ANTHROPIC_BASE_URL` apply in that repo, and says so; `--force` removes
  it anyway.
- **One unwritable repo does not stop the rest:** sync warns `skip <file>:
  cannot write it`, writes and ledgers the other repos, and exits 1. A file
  `--clean` cannot change keeps its ledger entry, so the next `--clean`
  retries it.
- **Symlinks are refused:** if `.claude` or `.claude/settings.local.json` is a
  symlink (a cloned repo can commit one), sync skips it without reading or
  replacing it.
- Writes are atomic, mode 0600, and `.claude/` is created 0700.
- If the file is not ignored by git, sync prints the line to add to
  `.gitignore` or `.git/info/exclude`. It never edits them.

**Limits for bare `claude`:**

- No secrets. `secret_env` variables are absent. With token-based GitHub auth
  (`github.auth: "sops:…"` or `gh:…`), `gh` finds an empty config and reports
  "not logged in" instead of falling back to another account (not yet
  verified hands-on). `git push` still works, because the git credential
  helper resolves the token when git asks. If you need `gh` itself in these
  sessions, give the profile `github: { home: <name> }` and log in once with
  `actas login <profile> --module github`.
- No second claude.ai login: `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_OAUTH_TOKEN`
  are ignored in project files, so bare `claude` uses the default login.
- Project `env` applies only after you accept the workspace trust prompt.
- There is no session record, so the agent's session profile comes from
  `ACTAS` in the synced file, which an agent could change: a guardrail
  against mistakes, not a boundary. Agents in these sessions cannot reach
  `power` profiles at all; a human runs those (see [docs/agents.md](agents.md)).
- A synced empty `ANTHROPIC_BASE_URL` is meant to cancel a gateway URL set
  elsewhere; how Claude Code treats the empty string is not yet verified.
  `CLAUDE_CODE_USE_*=""` does mean "off".

**Helpers use absolute paths.** `apiKeyHelper`, the git credential helper and
the SessionStart hook are written as `'<python>' '<actas>' …` with absolute
paths, because GUI apps often have a minimal `PATH`. If you move the actas
checkout, delete a virtualenv, or upgrade Python in a way that removes the
path, the helpers break: `actas run` and `actas doctor` notice and say
`run actas sync`, which rewrites them. Run actas with a system or Homebrew
`python3`, not a project virtualenv (actas warns when the interpreter looks
project-local). A uv or pipx install writes the installed `actas` command
itself instead (`'<…/bin/actas>' …`), so those helpers survive upgrades. Backend tools (`sops`, `gh`, `aws`, `op`, `security`) are
found through `PATH`, then `ACTAS_PATH_EXTRA`, then `/opt/homebrew/bin`,
`/usr/local/bin`, `~/.local/bin`, `~/.nix-profile/bin` and the Linux
equivalents.

---

## Claude Code in the Claude Desktop app

Desktop's local Code sessions read the same project settings files as the CLI,
so `actas sync` covers most of a profile. Two things are **app-wide** in
Desktop and cannot follow the project:

1. **Which account or gateway Desktop talks to.** A subscription profile uses
   whichever claude.ai account the app is signed in to (one per app). A
   gateway, API-key, Bedrock or Vertex profile needs Desktop's third-party
   inference configuration, and then **every** Desktop session uses it.
   Desktop does not call `apiKeyHelper` and ignores `ANTHROPIC_*` credentials.
2. **Variables already in Desktop's own environment.** A synced `env` value
   does not override a variable that Desktop's launch environment already sets.

### Gateway or API-key profiles: `actas desktop --print-3p`

```bash
actas desktop client-a --print-3p
```

prints (example values):

```json
{
  "inferenceProvider": "gateway",
  "inferenceGatewayBaseUrl": "https://llm-gateway.client-a.example",
  "inferenceGatewayAuthScheme": "bearer",
  "inferenceCredentialHelper": "/opt/homebrew/bin/python3",
  "inferenceCredentialHelperArgs": [
    "/Users/ada/.local/bin/actas",
    "secret",
    "--ref",
    "sops:client-a/token"
  ]
}
```

Enter these values in Claude Desktop under **Developer → Configure
Third-Party Inference**. Then:

- With a uv, pipx or Homebrew install, `inferenceCredentialHelper` is the
  installed `actas` command and the arguments start at `"secret"`.
- The credential helper is an absolute executable; its arguments name the
  secret by **reference** only. The token itself is never printed and never
  stored in the app's configuration. Desktop runs the helper when it needs
  the token.
- `inferenceGatewayAuthScheme` needs app version 1.3036.0 or later and
  `inferenceCredentialHelperArgs` needs 2.110.0 or later. For older apps,
  actas also prints (on stderr) a two-line `#!/bin/sh` wrapper: save it as a
  file with mode 0700 and use its path as `inferenceCredentialHelper`, with no
  arguments.
- Desktop caches the helper's answer for `inferenceCredentialHelperTtlSec`
  (default 3600 seconds), so a rotated token takes effect within an hour.
- `auth_header: x-api-key` in the profile's `claude:` block switches the
  scheme. For an `api-key` profile the base URL defaults to
  `https://api.anthropic.com` and the scheme to `x-api-key`.
- `--print-3p` works for `gateway` and `api-key` profiles. Bedrock and Vertex
  are configured in the app directly.

actas never writes this configuration itself: it is a global change, and you
decide when to make it.

### Per-project variables: saved environments

Desktop can start sessions from a **saved local environment** (`KEY=value`
lines). Only sessions started from that environment get those variables.
To fill one from a profile:

```bash
actas shell personal --format env
```

This prints `KEY=value` lines with **no secrets**: cloud variables,
`GH_CONFIG_DIR`, the git identity and credential helper (`GIT_CONFIG_*`,
numbered from 0), your `env:` values, `ACTAS` and `ACTAS_LEVEL`. With
`claude.home` it also prints `CLAUDE_CONFIG_DIR`; whether Desktop honours it
is not yet verified. The output is a snapshot: paste it again after you change
the manifest or move actas.

For a **gateway profile** the lines include `ANTHROPIC_BASE_URL` but no
credential (actas says so on stderr): Claude gets the gateway token only
through `apiKeyHelper`, which Desktop ignores. Use a saved environment for a
gateway profile only when the app already uses that gateway through
`actas desktop <p> --print-3p`; otherwise sessions from it fail to
authenticate, or send your claude.ai login to the gateway.

### Desktop limits

- One gateway, or one claude.ai account, per app, for all projects.
- No `apiKeyHelper`, no `ANTHROPIC_*` credentials, no `secret_env` values.
- Not yet verified: that Desktop runs the project SessionStart hook, and that
  synced `GIT_CONFIG_*` variables reach the commands Desktop's agent runs.
- Agent sessions in Desktop have no actas session record, so they cannot reach
  `power` profiles. `actas status --json` reports `"surface": "desktop"` when
  it finds a `Claude.app` ancestor process (not yet verified for every Desktop
  helper process).

---

## VS Code and JetBrains extensions

The Claude Code extensions start the `claude` CLI in your workspace, so a
synced `.claude/settings.local.json` applies exactly as for bare `claude`.
The most reliable path in any IDE is the integrated terminal: `actas run`.

**VS Code login check.** Before it starts a session, the VS Code extension
checks credentials using `claudeCode.environmentVariables` from VS Code's user
settings. To fill it from a profile:

```bash
actas desktop personal --vscode
```

prints `"claudeCode.environmentVariables": [{"name": …, "value": …}, …]` with
the profile's non-secret values only. User settings apply to every workspace
of that VS Code profile, so use this for your main identity and rely on
`actas sync` for per-project values.

For a gateway profile, the login check also needs a credential. Two options,
neither ideal:

- a global `apiKeyHelper` in `~/.claude/settings.json` (whether it satisfies
  the check is not yet verified; it also applies to every project that
  `actas sync` does not cover);
- the token itself in `claudeCode.environmentVariables` (plaintext in VS Code
  settings: not recommended).

**JetBrains.** The plugin runs the same CLI, so synced files apply. The IDE's
own environment comes from how it was launched (not yet verified with actas).

**Environment of the editor itself.** VS Code takes its environment from the
first instance; a second `code .` window inherits from the instance that is
already running. To start VS Code with a profile's environment, quit it first,
then:

```bash
actas exec client-a -- code ~/code/client-a
```

The same is assumed for Cursor and other VS Code-based editors (not yet
verified).

---

## Cloud sessions and Remote Control

**Cloud sessions** (claude.ai/code and the mobile app) run on
Anthropic's infrastructure. They read none of your local or user settings,
ignore gateway variables, and always use your claude.ai subscription. actas
cannot configure them.

For AWS access in a cloud session:

```bash
actas aws-handoff client-a          # in your own terminal; copies to the clipboard
```

It mints short-lived credentials for the profile's AWS profile (static keys are
first traded for a session token, `--duration` seconds, default 3600) and
copies a setup block to paste into the cloud session. The credentials then
live in that session's transcript, so keep roles narrow. It never logs in on
its own: if the SSO session expired it stops with exit code 4 (add `--login`).
It copies with `pbcopy` (macOS), `wl-copy` (in a Wayland session) or `xclip`
(with `DISPLAY` set); without one of those it prints only with `--stdout`. It
refuses to run inside an agent session.

**Remote Control** (driving a local session from claude.ai) is available only
for **subscription** profiles. It is off whenever an `apiKeyHelper`, an API
key, Bedrock/Vertex/Foundry or a non-Anthropic `ANTHROPIC_BASE_URL` is active,
so **gateway sessions cannot be driven from claude.ai**. `actas list` shows
`remote=yes` or `remote=no (<provider>)` per profile.

---

## Codex

**CLI.** Set `tool: codex` on a profile, or run `actas run -t codex`.

```yaml
profiles:
  partner:
    match: ~/code/partner-org
    tool: codex
    codex: { home: partner }        # CODEX_HOME=~/.local/state/actas/homes/codex/partner
    github: { auth: "sops:partner/gh_token" }
```

- `codex.home` gives each profile its own Codex login, config and history.
  `actas login partner` signs Codex in once (ChatGPT login by default; with
  `login: api`, actas pipes the `api_key` ref's value to
  `codex login --with-api-key` on stdin, never in argv).
- actas exports no Codex secret; Codex keeps its credential in its own home.
- actas 2.0 does not write Codex's `config.toml` (sandbox, approval policy,
  custom gateway providers). Write it by hand in the managed home; see
  [docs/recipes.md](recipes.md).
- Codex's default shell policy reportedly drops variables whose names contain
  `KEY`, `SECRET` or `TOKEN` from the commands it runs (not yet verified). So
  `GH_TOKEN` may not reach them; `git` still authenticates through the
  credential helper.

**Codex app and IDE extension.** They read `CODEX_HOME` from the environment
they were started with. Start them from an actas environment
(`actas exec <p> -- <app>`), or use the CLI. The desktop app reportedly sees
only `~/.zshenv` when started from the Dock (third-party reports, not yet
verified).

---

## Gemini CLI

```bash
actas run -t gemini        # or tool: gemini on the profile
```

- actas passes `--approval-mode default` for readonly and standard profiles
  and `--approval-mode auto_edit` for power profiles.
- `gemini.home` sets `GEMINI_CLI_HOME`. `gemini.auth: vertex` uses the profile's
  `gcp` identity and sets `GOOGLE_GENAI_USE_VERTEXAI=true` (variable name not
  yet verified for the current CLI); `gemini.api_key` puts `GEMINI_API_KEY` in
  the process environment, where the agent can read it. Prefer Vertex.
- Gemini CLI can also load `.env` files (such as `.gemini/.env`) from the
  project and its parents; a value there can override what actas set.
  `actas doctor` lists the ones it finds above the current directory.
- Agent detection relies on the process name `gemini` (the `GEMINI_CLI`
  marker is not yet verified).

---

## Cursor and other GUI apps

Apps started from the Dock, Finder, Spotlight or a desktop launcher get the
login session's environment, not your shell's. Three ways in:

1. **Launch the app through actas.** The app and everything it starts get the
   profile's full environment, **secrets included**:

   ```bash
   actas exec client-a -- /Applications/Cursor.app/Contents/MacOS/Cursor
   ```

   Single-instance apps may hand the request to an instance that is already
   running and ignore the new environment (verified for VS Code; not yet
   verified for others). Quit the app first.

2. **direnv** for editors with a direnv extension:

   ```bash
   actas setup direnv        # writes ~/.config/direnv/lib/actas.sh, which direnv loads
   echo 'use actas' >> ~/code/client-a/.envrc && direnv allow ~/code/client-a
   ```

   `use_actas` exports only non-secret values (`--no-secrets`). actas never
   writes `.envrc` itself. A direnv-exported `ACTAS` does not make your shell
   an agent; it only means that switching to a higher-level profile from that
   shell asks you to type the profile name.

3. **Apps with an environment setting:** paste the output of
   `actas shell <profile> --format env` (no secrets).

**Cursor's agent CLI** runs as `agent` (formerly `cursor-agent`). actas does
not recognise `agent` by name, and on macOS script-based CLIs show up as
`node` or `python`, so mark the launch itself:
`actas exec client-a -- env ACTAS_AGENT=cursor agent` (or a shell alias that
sets `ACTAS_AGENT` for that one command). Do not add `ACTAS_AGENT` to the
profile's `env:`: direnv and `actas shell` would export it into your own
terminal, which would then count as an agent and refuse `actas login`,
`actas sync` and other human-only commands. Cursor, Codex, Copilot and others
read `AGENTS.md`; `actas instructions` prints the rules to put there.

**Per-profile identity inside one running GUI app is generally not possible.**
One app process has one environment. Use one app instance per profile, or the
terminal.

actas never uses `launchctl setenv` or other global mechanisms.

---

## Limitations, in one place

- **GUI apps do not inherit your shell's environment.** Launch them through
  `actas exec`, use direnv-aware extensions, or paste `actas shell --format env`.
- **One claude.ai login per surface, except in the terminal.** `claude.home`
  (`CLAUDE_CONFIG_DIR`) gives a profile its own login, settings and history,
  wherever actas sets the environment (`actas run`, `actas exec`, or a shell
  after `eval "$(actas shell <profile>)"`). Desktop, the IDE extensions and a
  bare `claude` started without that environment use the default login
  (Desktop: the app's signed-in account; one claude.ai account per app). A new home
  starts empty: run `/login` in the first session; your user settings,
  plugins, MCP servers and `CLAUDE.md` from `~/.claude` are not there.
- **Helpers are absolute paths.** Moving actas or its Python breaks synced
  helpers until you run `actas sync` again (`actas doctor` reports it).
- **Gateway sessions cannot be driven from claude.ai.** Remote Control and
  cloud sessions are subscription-only.
- **Desktop has one gateway or account for all projects.** actas prints the
  configuration; it does not switch it per project.
- **Nested profiles inside another profile's repository** are not synced. Use
  `actas run` there.
- **No secrets outside `run` and `exec`.** Synced files, `--format env`,
  `--vscode` output and direnv never contain secret values.

---

## Not yet verified

These follow the tools' documentation or actas's design, but have not been
tested hands-on with actas:

- Claude Desktop runs project SessionStart hooks, passes synced `GIT_CONFIG_*`
  to its agent's commands, and honours `CLAUDE_CONFIG_DIR` from a saved
  environment.
- The Desktop third-party inference screen accepts the printed values as
  shown.
- A global `apiKeyHelper` satisfies the VS Code extension's login check.
- Claude Code's handling of an empty `ANTHROPIC_BASE_URL` in settings.
- `--settings` permission lists merge with the project's lists.
- `gh` with an empty `GH_CONFIG_DIR` and no token reports "not logged in".
- Single-instance apps other than VS Code reuse a running instance.
- Cursor behaves like VS Code for environment inheritance; JetBrains IDEs'
  environment handling.
- The Codex app sees only `~/.zshenv`; Codex's default environment filter.
- `GOOGLE_GENAI_USE_VERTEXAI` for the current Gemini CLI; the `GEMINI_CLI` and
  `CODEX_SANDBOX*` agent markers.
- `"surface": "desktop"` detection for every Claude Desktop helper process.
