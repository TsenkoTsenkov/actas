# Security

## Reporting a vulnerability

Please report security issues privately through
[GitHub security advisories](https://github.com/TsenkoTsenkov/ccenv/security/advisories/new),
not in a public issue.

## The short version

- **Credentials are the boundary.** A read-only token, a `ReadOnlyAccess` role
  or a `Reader` assignment is enforced by the server. Give each profile the
  narrowest credentials that do the job.
- **Agent rules are a seatbelt.** ccenv's permission rules, agent detection,
  refusals and typed confirmations stop a mistaken or prompt-injected agent
  that follows its instructions. They do not stop code that is determined to
  get around them.
- **Secrets are references.** The manifest holds `scheme:path` refs, never
  values. ccenv resolves them when a process needs them and never writes a
  value to disk in the clear.

## What ccenv is, and what it is not

ccenv runs on one machine, as you. It decides which identity (LLM provider,
cloud accounts, GitHub account, git identity, env vars) one process gets,
and it keeps those identities from leaking into each other.

Every check ccenv makes runs in your user account, like the agent it guards.
So:

- **Credentials are the boundary.** What a token or role allows on the server
  is what an agent holding it can do.
- **Agent rules are a seatbelt.** Claude Code's own documentation says Bash
  deny rules are not a security boundary around the program.
- **ccenv's in-process checks** (agent detection, the session record, refusals,
  typed confirmation, the audit log) stop a mistaken or prompt-injected agent
  that follows its instructions or retries naively. They do **not** stop
  same-user code that is determined. Such code can call
  `aws --profile <anything>` directly, read the age key and the AWS SSO cache
  (which may be able to mint every role on that SSO portal; not yet verified),
  use any `gh` account in the keyring, run `git credential fill`, or forge
  ccenv's state files.
- **Real boundaries** are narrowly scoped credentials, a separate SSO start URL
  or MFA for elevated roles, a hardware-backed key for power secrets, and a
  devcontainer or VM for code you do not trust.

## Threat model

### What ccenv protects against

**Secrets at rest in the clear.**
- A secret is a ref: `sops:` (an age-encrypted file), `keychain:` (macOS
  Keychain), `op://` (1Password), `gh:` (a token from the GitHub CLI's
  keyring) or `cmd:` (a command you name in the manifest).
- Values are read into memory and handed to the child process. ccenv never
  writes a decrypted copy. Generated settings files, synced
  `.claude/settings.local.json` files, the SessionStart note, `ccenv desktop`
  output, `ccenv shell --format env|json` and the audit log hold names and
  refs only. Tests check every generated file for a known secret value.
- The sync ledger holds the keys ccenv wrote in each synced file and, so
  `sync --clean` can put them back, the values of your own keys it replaced
  there (copied from that same local settings file). For an env name that
  looks like a credential (`TOKEN`, `KEY`, `SECRET`, `PASS`, `CREDENTIAL`,
  `AUTH`) it stores only that the key existed, never the value; `--clean`
  then removes ccenv's value and tells you the original was overwritten.
  Other values are stored as written, including a top-level `apiKeyHelper`
  command of yours that a gateway profile replaced: if yours embeds a key,
  move the key out of the command (the ledger is 0600, like the settings
  file it came from).
- sops decrypts the secrets file once per command, in memory.

**Secrets in argv and shell history.**
- Backends run from an argv list, never through a shell, with a 30-second
  timeout.
- `ccenv secret set <ref>` prompts for the value (or reads stdin). sops gets it
  on stdin (`--value-stdin`); an older sops without that flag gets it in argv
  briefly, and ccenv warns. For `keychain:` refs, `security` prompts for the
  value itself.
- `codex login --with-api-key` gets the key on stdin.
- The v1 form `ccenv secret ENV KEY VALUE --set` still accepts a value
  argument, with a warning; leave it out to be prompted.

**Leaks between contexts.**
- Before a session starts, ccenv clears the shell's variables for every module
  and tool the manifest uses (for example `AWS_PROFILE`, `AWS_ACCESS_KEY_ID`,
  `GH_TOKEN`, `ANTHROPIC_*`, `CLAUDE_CODE_USE_*`), and every variable some
  profile sets through `secret_env` (so client A's `NPM_TOKEN` does not
  reach client B), and prints what it cleared. Plain `env:` values are not
  secrets and are not cleared for other profiles (list a variable in a
  profile's `unset:` to keep it out), and session basics such as
  `SSH_AUTH_SOCK`, `DISPLAY`, `EDITOR`, `LC_*` and `XDG_*` are never cleared
  this way. Manifests still on version 1
  clear only the variables v1 managed (AWS ones only for environments with an
  `aws:` block), so there one environment's `secret_env`, or your shell's
  `AWS_PROFILE`, reaches the next session unless you migrate.
- `aws: null` (likewise `gcp`, `azure`, `github: null`) points the tool at an
  empty ccenv-owned config, so it cannot fall back to `[default]`, the global
  gcloud account, `~/.azure` or the gh keyring.
- Token-based GitHub auth fails closed: `gh` gets an empty config directory,
  so a missing or stripped token means "not logged in", not your keyring's
  active account (not yet verified with the real `gh`). The exception is the
  human's own `shell --no-secrets` output (direnv, mise) outside agent
  sessions: it leaves `gh` on its keyring login, since the token is left out
  on purpose there.
- The empty `_off` homes behind `null` and fail-closed GitHub are shared by
  every such profile, so they must stay empty: a `gh auth login`,
  `aws configure sso`, `gcloud auth login` or `az login` run inside one would
  log all of them in. `ccenv run`, `exec`, `shell`, `login` and `sync` move a
  non-empty `_off` home aside (with a warning) and start a new empty one;
  `ccenv status` warns and `ccenv doctor` reports it as an error. Log in in a
  named `home` instead.
- A secret that cannot be read leaves its variable unset, never the shell's
  value.
- The gateway token reaches Claude through `apiKeyHelper` and is not exported
  into the session (the v2 default; manifests still on version 1 keep
  exporting it).

**Global state changes.** ccenv never runs `gh auth switch`,
`gcloud config set`, `kubectl config use-context` or `launchctl setenv`; never
edits `~/.gitconfig`, `~/.aws/config`, `.envrc`, `.git/info/exclude` or your
`AGENTS.md`/`CLAUDE.md`; and never writes Claude Desktop's configuration.
`az account set` runs only inside a profile's own `azure.home`. Identity is
set per process.

**Injection from configuration.**
- Profile names, env var names and ref parts are checked against strict
  patterns, so a ref cannot be mistaken for a command-line option. `$` is
  rejected in `op://` refs.
- Every value placed into a shell string (helper commands, `ccenv shell`
  output) is quoted with `shlex.quote`; fish output uses its own quoter.
- Helpers embed the ref and never parse the manifest.
- `cmd:` refs come only from your own manifest; `ccenv doctor` lists each one.
- The git credential helper prints nothing if a value or username contains CR,
  LF or NUL.

**Agents acting outside their profile** (a seatbelt, see above).
- ccenv detects agent context from environment markers (`CLAUDECODE`,
  `CLAUDE_CODE_SESSION_ID`, `CCENV_AGENT`, …) and from the names of ancestor
  processes (`claude`, `codex`, `gemini`, …).
- `ccenv run` writes a session record
  (`~/.local/state/ccenv/sessions/<pid>.json`) tied to the agent process and its
  start time, so a reused pid does not inherit a session.
- An agent may `ccenv exec` only its session's profile, or the profiles listed
  in that profile's `agents_may_use`, at the same or a lower level, and never a
  `confirm` profile. A nested `ccenv run` keeps the outer session's list, so
  hops do not chain. Inside a `ccenv run` session, changing `CCENV`,
  `CCENV_ROOT`, `CCENV_STATE`, `XDG_STATE_HOME`, `HOME` or the directory does
  not change that: ccenv also keeps a copy of the record under your passwd
  home's `~/.local/state/ccenv/sessions/` (from the user database, not
  `$HOME`) and looks there first, so it is found even with `CCENV_SESSION`
  removed and a record forged in a moved state directory does not beat it
  (code that forges or deletes that copy gets around this). Without a session record (bare
  `claude`, Desktop, IDE extensions), the session profile comes from `CCENV`,
  which the agent could change; that is why such sessions can never reach a
  `power` profile. An agent with neither a record nor `CCENV` may use only the
  readonly profile of its own working directory. Both of these are guardrails
  against mistakes, not a boundary: an agent can set `CCENV` or `cd`.
- `ccenv secret` and `ccenv git-credential` are helpers that Claude and git
  call, so they are not refused in agent context: they print any ref's value
  and are only audit-logged. For Claude Code with a v2 manifest, the deny
  rules below cover them; v1 manifests, Codex, Gemini and other agents have
  no such rule.
- Logins, `sync`, `init`, writing secrets, `shell` with secrets, `aws-handoff`
  and `migrate --write` are human-only.
- Every refusal prints a first stderr line `ccenv-stop: code=… reason=…` and an
  `ask_user` text built from argv with `shlex.join`, with a warning when the
  command runs a shell or interpreter. The human runs the command in a
  separate terminal. There is no in-session approval and no bypass variable.
- A human running a `confirm` profile, or a profile above the shell's `CCENV`
  level, types the profile name on `/dev/tty`.
- For version 2 manifests, the generated Claude settings deny secret-reading
  commands (`ccenv secret`, `sops`, `gh auth token`, `git credential`, …), deny
  reads of credential files (the age key, `~/.aws/sso/cache`, gh `hosts.yml`,
  the credential files in managed homes) and deny edits to ccenv's config and
  state directories. The profile's level decides whether `git push`,
  `terraform apply` and similar commands are denied, asked about or allowed.
- An audit log, `~/.local/state/ccenv/audit.jsonl` (mode 0600, append mode),
  records `run`, `exec`, logins, `sync`, `aws-handoff`, secret writes, every
  refusal, declined confirmations, and secret reads by agents: names, the command's first word and an
  argv hash, never values. Read it with
  `tail -n 50 ~/.local/state/ccenv/audit.jsonl`. Same-user code can edit it.

**Unexpected credential prompts.** `ccenv run` warns about expired AWS
credentials but never opens a browser unless you pass `--login`. In agent
sessions, `gh`, git, gcloud and the AWS pager are set to non-interactive.

### What ccenv does not protect against

- **Code running as your user.** It can do everything ccenv can: read the age
  key, the gh keyring and the AWS SSO cache, call cloud CLIs with any profile,
  or edit ccenv's files.
- **A determined agent.** Agent rules and refusals are a seatbelt. Detection
  runs `/bin/ps` (or `/usr/bin/ps`; `PATH` only when neither exists), but a
  process started outside the agent's process tree has no agent ancestry.
  npm-installed agent CLIs show up as `node` and are detected through
  their environment markers only. Commands you type with `!` inside Claude
  Code run in the agent's context and count as agent commands; run elevated
  commands in a separate terminal.
- **Secrets inside a session.** These are in the process environment, readable
  by the session and everything it runs, including the model's shell
  commands: `secret_env` values, GitHub tokens (`GH_TOKEN`, `GITHUB_TOKEN`),
  `claude.oauth_token`, `claude.bearer_token`, a token with
  `claude.export_token: true`, and `gemini.api_key`. Use narrow tokens, and see
  `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` in the checklist.
- **The git credential helper is not a boundary.** It keeps tokens out of the
  environment and out of files, but any same-user process can run
  `git credential fill`.
- **Pasted credentials.** `aws-handoff` output pasted into a cloud session is
  stored in that session's transcript. ccenv trades static keys for a session
  token first and defaults to one hour, but the credentials work until they
  expire.
- **Apps launched through `ccenv exec`.** A GUI app started this way gets the
  profile's full environment, secrets included, and passes it on to everything
  it starts.
- **Tools' own credential stores.** Logins made with `ccenv login` (Codex, gh,
  gcloud, az, a second Claude home) are stored by those tools in their usual
  format, inside ccenv's per-profile home directories. With `codex.login: api`
  Codex writes the key in plain text to its home's `auth.json`, so ccenv
  refuses that login without a `codex.home` (it would land in the global
  `~/.codex`).

### What ccenv writes to disk

| Path | Contents | Mode |
|---|---|---|
| `~/.config/ccenv/environments.yaml` | the manifest: names, directories, refs | 0600 when ccenv writes it |
| `~/.config/ccenv/secrets.enc.yaml` | sops-encrypted secrets (only for `sops:` refs) | 0600 when ccenv creates it |
| `~/.cache/ccenv/<profile>.<hash>.settings.json` | Claude settings for `run` (no secret values), pruned after 7 days | 0600 |
| `~/.cache/ccenv/<profile>.context.txt` | the SessionStart note | 0600 |
| `<repo>/.claude/settings.local.json` | synced non-secret settings | 0600 |
| `~/.local/state/ccenv/sync-ledger.json` | which keys ccenv wrote where; the values of your own non-credential keys it replaced there (see above) | 0600 |
| `~/.local/state/ccenv/sessions/<pid>.json` | session records; also written under your passwd home's `~/.local/state/ccenv/sessions/`, which no variable moves | 0600 |
| `~/.local/state/ccenv/audit.jsonl` | the audit log (no values) | 0600 |
| `~/.local/state/ccenv/homes/<tool>/<name>/` | per-profile tool homes, and the empty `_off` homes used by `null` | 0700 |

Writes are atomic (temp file, `fsync`, rename). Directories ccenv creates are
0700. `CCENV_ROOT`, `CCENV_STATE`, `XDG_CACHE_HOME` and `XDG_STATE_HOME` move
these locations, except the fixed copy of the session records.

## Hardening checklist

**Credentials: the real boundary**
- [ ] Use **read-only tokens** for readonly profiles: a fine-grained GitHub PAT
      with Contents, Metadata and Pull requests set to read, stored with
      `ccenv secret set <ref>`. A `gh:` account token usually has full repo
      scope; `ccenv doctor` points this out for readonly profiles.
- [ ] Give **power roles a separate AWS SSO start URL, or require MFA**
      (`mfa_serial`), so an agent holding the everyday SSO session cannot
      assume them. The same idea applies to Azure PIM and GCP impersonation.
- [ ] Mark admin profiles `level: power` and `confirm: true` (`ccenv doctor`
      warns about power without confirm). `confirm` profiles are not synced
      and always need a human.
- [ ] List only the profiles an agent really needs in `agents_may_use`.
- [ ] Use `null` (`aws: null`, `github: null`, …) for profiles that must not
      see an account.

**Secrets**
- [ ] `chmod 600` the age key (`~/.config/sops/age/keys.txt`) and back it up.
      For power secrets, consider a hardware-backed age identity such as
      `age-plugin-se` (Secure Enclave with Touch ID).
- [ ] Keep `~/.config/ccenv` private (`ccenv doctor` warns; `ccenv doctor --fix` runs the `chmod 700`) and out
      of public repositories: the manifest names your clients and accounts.
- [ ] Put secrets in `secret_env` or refs, never in `env:`. `env` values are
      written to synced settings files; `ccenv doctor` warns about names that
      look secret.
- [ ] Leave `claude.export_token` off (the v2 default) so the gateway token
      reaches Claude only through `apiKeyHelper`. After `ccenv migrate`, delete
      the `export_token: true` lines it keeps for compatibility.
- [ ] Prefer `keychain:` or `op://` refs where they fit, and review every `cmd:`
      ref (`ccenv doctor` lists them).
- [ ] Store values with `ccenv secret set <ref>` and no value argument.

**Agent sessions**
- [ ] For profiles that put secrets in the process environment, add
      `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: "1"` to the profile's `env:`. Claude
      Code then strips credentials from the environment of Bash, hook and
      stdio MCP subprocesses. Per Claude Code's documentation it leaves GitHub
      tokens in place, so keep those read-only where you can.
- [ ] Run elevated commands in a separate terminal, never with `!` inside the
      agent session.
- [ ] Add `.claude/settings.local.json` to `.gitignore` (or
      `.git/info/exclude`); `ccenv sync` prints the line when it is missing.
- [ ] Look at the audit log now and then:
      `tail -n 50 ~/.local/state/ccenv/audit.jsonl`.
- [ ] Run untrusted code, or agents with broad credentials, in a devcontainer
      or VM.

**Cloud sessions**
- [ ] Keep `aws-handoff` roles narrow; for SSO, keep the role's session
      duration short (`--duration` applies only to static IAM keys). Prefer
      SSO profiles over static IAM keys.

## Not yet verified

These are described above as expected behaviour but have not been tested
hands-on:

- that the AWS SSO token cache can mint every role on its portal;
- `gh` with an empty `GH_CONFIG_DIR` and no token reports "not logged in";
- Codex's default environment filter (variables whose names contain `KEY`,
  `SECRET` or `TOKEN` dropped from the commands it runs);
- `security add-generic-password -w` prompting when stdin is not a terminal;
- Claude Code merging the generated `--settings` permission lists with a
  project's own lists;
- permission rule paths that contain spaces (for example an age key under
  `~/Library/Application Support`);
- the Linux `ps` output format (checked in CI only), and the agent markers for
  Codex (`CODEX_SANDBOX*`) and Gemini (`GEMINI_CLI`).
