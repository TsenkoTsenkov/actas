# Migrating from v1 to v2

**Short version:** install v2 and keep working. Your v1 manifest runs unchanged.
v2 is also where ccenv was renamed to actas; an existing ccenv setup keeps
working, after two steps that the rename needs right away: put `actas` on your
PATH and replace an installed ccenv skill (see [Coming from ccenv](#coming-from-ccenv)).
Convert it to version 2 when you want roles, levels and the new modules:

```bash
actas doctor                       # 1. v2 reads your v1 file; check it
actas sync --dry-run && actas sync # 2. refresh synced settings (new helper strings)
actas migrate                      # 3. print the v2 version; nothing is written
actas migrate --write              # 4. switch (the old file is kept as environments.v1.yaml)
actas sync                         # 5. re-sync with the v2 rules
```

Your secrets file is never read or changed by the migration, and every secret
keeps its v1 location.

---

## Version 2 in four rules

1. **Later layers win, maps merge.** `defaults`, then each `use:` layer, then
   the profile itself. Nested maps merge key by key.
2. **`null` means off: the tool sees no account.** `aws: null` points the AWS
   CLI at an empty config, so it cannot fall back to `[default]`.
3. **Compose upward.** A profile is at least as privileged as anything it
   `use:`s; `level: readonly` on top of a power role is an error.
4. **A secret is always `scheme:path`.** For example `sops:client-a/token`,
   `keychain:actas/client-a`, `op://vault/item/field`, `gh:ada-client-a`.

The full reference is in [docs/manifest.md](manifest.md).

---

## Step 1: upgrade the binary, keep the v1 manifest

actas v2 recognises a v1 file (no `version:`, or `version: 1`) and translates
it in memory on every load. You do not need to change anything to keep
working. Some behaviour changes anyway, mostly fixes:

**Environment**

- Variables that v1 managed are now **cleared from your shell** before a
  session starts, so a stale export cannot leak in, but only for what the
  environment configures:
  - always: `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`,
    `ANTHROPIC_CUSTOM_HEADERS`, `CLAUDE_CODE_USE_*`, `CLAUDE_CODE_OAUTH_TOKEN`,
    and `GIT_CONFIG_*` pairs added by an outer actas;
  - with an `aws:` block: `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`,
    `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`,
    `AWS_REGION`, `AWS_DEFAULT_REGION`;
  - with `github.account` or `secret_env.GH_TOKEN`: `GH_TOKEN`, `GITHUB_TOKEN`;
  - with `github.name`: `GIT_AUTHOR_NAME`, `GIT_COMMITTER_NAME`; with
    `github.email`: `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_EMAIL` (git would let
    them override the identity the environment sets).

  An environment **without an `aws:` block keeps your shell's `AWS_PROFILE`**
  (and other `AWS_*`), exactly as v1 did; without a GitHub account it keeps
  your shell's `GH_TOKEN`. Isolating them is a v2 opt-in (`aws: null`, after
  `actas migrate`). `actas run` prints what it cleared. Nothing else is touched
  for v1 files (`CLAUDE_CONFIG_DIR`, `OPENAI_API_KEY`, `GOOGLE_*`, `ARM_*`
  pass through).
- Subscription sessions **unset** the three `ANTHROPIC_*` variables instead of
  setting them to `""`. Settings also blank `CLAUDE_CODE_USE_MANTLE` and
  `CLAUDE_CODE_USE_ANTHROPIC_AWS`.
- New variables in sessions: `ACTAS_LEVEL`, `ACTAS_SESSION`, `ACTAS_AGENT`,
  `ACTAS_GIT_BASE`; in agent sessions also `GH_PROMPT_DISABLED=1`,
  `GIT_TERMINAL_PROMPT=0` and `AWS_PAGER=`. v1's `CCENV` is now `ACTAS` (see
  [Coming from ccenv](#coming-from-ccenv)).
- `secret_env.GH_TOKEN` now also sets `GITHUB_TOKEN`, and git gets a credential
  helper for it. With both `github.account` and `secret_env.GH_TOKEN`, both
  variables are the PAT (v1: `GITHUB_TOKEN` was the account's token).
- A secret that cannot be read leaves its variable **unset** (with a warning),
  rather than keeping whatever your shell had.
- Exactly one trailing newline is stripped from secret values.
- `env:` values: `null` unsets the variable; invalid variable names and unknown
  `provider:` values are errors; booleans give a warning. A string
  `claude_args` is split like a shell would, with a warning.
- A key-name field (`secret_env` value, `gateway.secret`,
  `gateway.secret_from`) that holds what looks like a pasted token (for
  example `ghp_` plus 36 letters and digits, an `AKIA…` key, or anything
  longer than 64 characters) is an error that does not repeat the value. Key
  names such as `npm_token` or `ghs_deploy` load as before.

**Settings and helpers**

- `apiKeyHelper`, `awsAuthRefresh` and the git helper use absolute paths and the
  `actas secret --ref 'sops:<env>/<key>'` form. Old helper strings in synced
  files keep working (v1's `ccenv secret ENV KEY` runs through the deprecated
  `ccenv` alias); the next `actas sync` rewrites them.
- `run`'s generated settings now repeat every non-secret variable of the
  session and add a SessionStart hook that prints a one-line note about the
  profile. The file is named by content hash in `~/.cache/actas/` (mode 0600,
  pruned after 7 days).

**sync**

- Targets use the same longest-match rule as `run`.
- Files are written at the **git repository root**, where Claude Code reads
  them. A nested directory that belongs to another profile is skipped with a
  warning. A v1-written file left there, or in a git worktree of the repo, is
  reported and kept up to date with that directory's profile (Claude may still
  read keys the root does not set). A matched folder that is not a repo also
  gets the file in each git repo up to two levels below it that resolves to
  the same profile.
- Synced files now carry **every non-secret session variable**, not only
  v1's provider and AWS keys: the git identity (`user.name`/`user.email`), a
  github.com credential helper that replaces your global one and reads
  `gh:<account>` through actas, `ACTAS`/`ACTAS_LEVEL`, `GH_PROMPT_DISABLED`,
  `GIT_TERMINAL_PROMPT` and `AWS_PAGER`. Bare `claude`, IDE and Desktop
  sessions in those repos then commit and push as the profile's account.
- Writes are atomic with mode 0600; a ledger records which keys actas owns,
  so your own keys are kept and stale ones removed. v1-written files are
  adopted.
- New: `--dry-run`, and `--clean`, which removes what actas wrote in files no
  profile syncs any more (it keeps, unless you add `--force`, a file whose
  removal would let `~/.claude/settings.json`'s `apiKeyHelper` or
  `ANTHROPIC_BASE_URL` apply there). `--shared` is gone (it exits with an error).
- sync prints the `.gitignore` line to add; it never edits git files.

**Commands**

- New: `exec`, `status` (alias `whoami`), `desktop`, `instructions`, `migrate`.
- `run` checks only the target profile's AWS login and only warns.
- `aws-handoff` no longer logs in by itself: expired credentials exit with code
  4 unless you pass `--login`. Without a clipboard tool it needs `--stdout`.
- `--` passes arguments only to `run` and `exec`; elsewhere it is a usage error.
- GitHub logins containing `_` (Enterprise Managed Users) are accepted.
- `doctor` exits 1 when the manifest has errors.

**Agents**

actas now recognises when an AI agent calls it. Inside an agent session:

- `run`/`exec` of a profile other than the session's stop with exit code 3 and
  a first stderr line starting `actas-stop:`;
- `login`, `sync` (including `--dry-run`), `init`, `secret --set` /
  `secret set`, `shell` with secrets, `aws-handoff` and `migrate --write` are
  human-only and stop the same way.

The agent is expected to show you the printed `ask_user` text; you run the
command yourself in a separate terminal. See [docs/agents.md](agents.md).

**Unchanged for v1 files** (these change only after `actas migrate`):

- no built-in agent permission rules are written;
- the gateway token is still exported into the session;
- `gh` does not fail closed (no empty `GH_CONFIG_DIR`): with the token
  missing, gh uses its keyring's active account as in v1. Git HTTPS to
  github.com always uses actas's credential helper, though, so a missing gh
  token now fails `git push` instead of falling back to your global helper.

---

## Step 2: what `actas migrate` does

```bash
actas migrate
```

- Prints the version 2 translation of your v1 manifest on stdout (YAML; JSON
  if neither PyYAML nor `yq` is available) and validates it first.
- Lists on stderr what changes once you switch, plus suggestions.
- **Never reads or edits `secrets.enc.yaml`.** Every secret becomes an explicit
  ref to its v1 location (`sops:<environment>/<key>`, or
  `sops:<secret_from>/<secret>` for a shared gateway token), so no secret moves.
- Writes nothing. On a file that is already version 2 it says so and exits.

```bash
actas migrate --write
```

- Copies the current file to `~/.config/actas/environments.v1.yaml` and writes
  the new one (mode 0600). It refuses if `environments.v1.yaml` already exists.
- Is human-only: it refuses inside an agent session.
- Does not carry over comments from the v1 file.

### How v1 keys translate

| v1 | v2 |
|---|---|
| `environments:` | `profiles:` |
| `defaults:` (shallow-merged into each environment) | merged into each profile; `migrate` writes no `defaults:` |
| `provider: subscription` | the default; nothing written |
| `provider: gateway` + `gateway.base_url` | `claude: { provider: gateway, base_url: … }` |
| `gateway.secret` / `gateway.secret_from` | `claude.token: "sops:<secret_from or env>/<secret or token>"`, plus `claude.export_token: true` |
| `claude_args: [...]` | `claude.args: [...]` |
| `github.account: A` | `github.auth: "gh:A"` |
| `github.name`, `github.email` | `git.name`, `git.email` |
| `secret_env.VAR: key` | `secret_env.VAR: "sops:<env>/key"` |
| `secret_env.GH_TOKEN: key` | `github.auth: "sops:<env>/key"` (and a matching `GITHUB_TOKEN` entry is dropped) |
| `aws`, `env`, `match` | unchanged |
| `agents_may_use` (in `defaults:` or an environment) | `agents_may_use` on each profile (the only v2 key a v1 file accepts) |

Example: this v1 environment

```yaml
client-a:
  match: [~/code/client-a]
  provider: gateway
  gateway: { base_url: https://llm-gateway.client-a.example, secret: token }
  aws: { profile: client-a-dev, region: eu-west-1 }
  github: { account: ada-client-a, name: Ada Example, email: ada@client-a.example }
```

becomes

```yaml
version: 2
profiles:
  client-a:
    match: ["~/code/client-a"]
    claude:
      provider: "gateway"
      base_url: "https://llm-gateway.client-a.example"
      token: "sops:client-a/token"
      export_token: true
    aws:
      profile: "client-a-dev"
      region: "eu-west-1"
    github:
      auth: "gh:ada-client-a"
    git:
      name: "Ada Example"
      email: "ada@client-a.example"
```

### What changes once the file says `version: 2`

`actas migrate` lists these on stderr:

- **Built-in agent rules apply.** Each profile gets its level's preset
  (`standard` unless you set `level:`): Claude asks before `git push`,
  `terraform apply` and similar, and is denied secret-reading commands and
  credential files. Add `permissions: { builtin: false }` to a profile to keep
  v1's "no rules".
- **Wider scrubbing.** The shell's variables are cleared for every module the
  manifest references (for example all Claude provider variables, all
  `CLOUDSDK_*` once you use `gcp`, and `GIT_AUTHOR_*`, `GIT_COMMITTER_*` and
  `GIT_SSH_COMMAND` once some profile has a `git:` block, which `migrate`
  writes for `github.name`/`email`), in every profile. `migrate` writes
  `aws: inherit` for environments that had no `aws:` block, so they keep your
  shell's `AWS_PROFILE` as in v1; change it to `aws: null` to isolate them
  (an empty AWS config, so the aws CLI cannot fall back to `[default]`).
  `inherit: [VAR]` keeps single variables.
- **GitHub token auth fails closed.** With `github.auth` set, `gh` gets an
  empty config directory, so a missing token means "not logged in" instead of
  your keyring's active account. Use `github.home` if you need gh's own config.
- **Boolean `env` values are errors.** Quote them: `"1"`, `"true"`.
- **The gateway token stays exported** because `migrate` writes
  `export_token: true`. Remove that line to pass the token only through
  `apiKeyHelper` (recommended).

Suggestions it may print:

- environments that share one `claude:` block: move it into a role and `use:` it;
- environments that had no `aws` block (written as `aws: inherit`, which keeps
  v1's behaviour): change it to `aws: null` to isolate them (an empty AWS
  config).

---

## Step by step

1. **Install v2** the same way you installed v1 (for a git checkout:
   `git -C <checkout> pull`). The checkout keeps `bin/ccenv` as a link to
   `bin/actas`, and a uv/pipx install keeps a `ccenv` command, so existing
   helpers keep resolving. Then do the [steps the rename needs right
   away](#right-away) (the `actas` command, the skill).
2. **Check:** `actas doctor` and `actas list`. Fix any errors it reports.
3. **Refresh synced files:** `actas sync --dry-run`, then `actas sync`. This
   rewrites helper strings to the absolute `--ref` form and starts the ledger.
4. **Preview the conversion:** `actas migrate > /tmp/actas-v2.yaml` and read
   it together with the stderr notes.
5. **Switch:** `actas migrate --write`.
6. **Verify:** `actas doctor`, then `actas status --why` in a few project
   directories. `actas status --why` shows where each value comes from.
7. **Re-sync:** `actas sync` writes the v2 agent rules into synced files.
8. **Tidy up** (optional, one change at a time, `actas doctor` after each):
   - delete `export_token: true` from gateway profiles;
   - move shared blocks into `roles:` and `use:` them;
   - set `level: readonly` where the credentials really are read-only, and
     `level: power` plus `confirm: true` for admin profiles;
   - turn `aws: inherit` into `aws: null` (or add `gcp: null`, …) where a
     profile must not see an account;
   - add `agents_may_use:` where an agent may run another profile's commands.

Starting fresh instead? `actas init` writes a version 2 starter, so there is
nothing to migrate.

---

## Rollback

**Back to the v1 manifest, keeping v2 actas** (the safe, common case):

```bash
cp ~/.config/actas/environments.yaml ~/actas-v2-draft.yaml          # optional: keep your v2 edits
mv ~/.config/actas/environments.v1.yaml ~/.config/actas/environments.yaml
actas sync                                                           # removes the v2 rules from synced files
```

v2 runs the v1 file exactly as in step 1. Secrets are untouched either way.

These commands use `~/.config/actas`. If you never moved your config
directory after the rename, it is still `~/.config/ccenv`: use that path
instead (`actas doctor` says which one is in use, on its `config dir` line).

**Back to the v1 binary:** clean up first, because v1 does not understand v2's
helper strings (`secret --ref`, `git-credential`, the SessionStart hook):

```bash
# with v2: an empty manifest makes every synced file stale, so --clean removes
# what actas wrote (--force: also files that hide ~/.claude/settings.json routing)
# (~/.config/ccenv instead of ~/.config/actas if you never moved it)
mv ~/.config/actas/environments.yaml ~/actas-v2.yaml
echo '{"version": 2, "profiles": {}}' > ~/.config/actas/environments.yaml
actas sync --clean --force
# restore the v1 manifest as above, then reinstall v1, which is called ccenv
# and reads only ~/.config/ccenv (move the directory back if you moved it)
# (for a git checkout: git -C <checkout> checkout <previous commit>)
ccenv sync                  # with v1
```

Optional leftovers you can delete: `~/.local/state/actas/` (ledger, audit log,
session records, managed homes; homes hold logins you made with `actas login`)
and `~/.cache/actas/`.

---

## Coming from ccenv

actas was called **ccenv** before 2.0. The rename changes names only. Your
config, secrets, synced files and the `ccenv` command keep working, but two
things need a step as soon as you upgrade: agents are now told to run `actas`,
so it must be on your PATH, and an installed ccenv skill must be replaced
(see [Right away](#right-away)).

**Automatic** (nothing to do):

- **Config and state.** While `~/.config/actas` does not exist and
  `~/.config/ccenv` does, actas uses `~/.config/ccenv`; the same for
  `~/.local/state/ccenv` (ledger, audit log, session records, managed homes).
  The cache is rebuilt in `~/.cache/actas`.
- **Variables.** Every `ACTAS*` variable actas reads falls back to the old
  `CCENV*` spelling when the new one is unset: `CCENV=<profile>` from a synced
  settings file or direnv, `CCENV_ROOT`, `CCENV_STATE`, `CCENV_AGENT`,
  `CCENV_PATH_EXTRA` and so on. actas writes only `ACTAS*`, and `run`, `exec`
  and `shell` drop a stale `CCENV`, `CCENV_LEVEL` and `CCENV_GIT_BASE` (and
  `CCENV_AGENT` when they set `ACTAS_AGENT`), so read `$ACTAS` now where you
  read `$CCENV`. For the agent gate the old names count exactly like the new
  ones (an agent cannot get around it by setting `CCENV_ROOT`).
- **Running sessions.** While the state directory is still
  `~/.local/state/ccenv`, records written by a `ccenv run` that is still open
  are found, so its agents keep their reach. Once you move it (step 2 below),
  those agents are refused with `override`, which is why the move needs no
  session open.
- **Synced settings files.** Files written by ccenv (the `ccenv secret ENV
  KEY` or `.../ccenv secret --ref` helper, the SessionStart hook, `CCENV*`
  variables) are recognised as tool-written. The next `actas sync` rewrites
  them in the new form and removes the `CCENV*` variables.
- **The `ccenv` command** is a deprecated alias of the same program (a second
  console script in uv/pipx installs, `bin/ccenv` in a git checkout), so the
  helper strings in files synced before the rename keep working. Run from a
  terminal it reminds you of the new name. `use ccenv` in an `.envrc` keeps
  working too (`contrib/direnvrc` defines it as an alias of `use actas`).

**Getting the `actas` command.** The package is now called `actas`:

- uv or pipx: `uv tool uninstall ccenv && uv tool install
  git+https://github.com/TsenkoTsenkov/actas` (or the same with `pipx`). The
  new package installs both `actas` and the `ccenv` alias, at the same place,
  so helper strings in synced files keep resolving.
- A git checkout: `git -C <checkout> pull`, then
  `ln -s <checkout>/bin/actas ~/.local/bin/actas`. An existing
  `~/.local/bin/ccenv` link keeps working: `bin/ccenv` in the checkout is a
  link to `bin/actas`.
- install.sh: run the new one; it installs `actas` next to your old `ccenv`
  file and leaves that file alone.

### Right away

Do these when you upgrade; `actas doctor` warns about each until it is done.

1. **The `actas` command on your PATH** (above). The SessionStart note, the
   agent instructions and the refusals all say `actas ...`; with only the
   `ccenv` alias installed, agents and you get `command not found`
   (doctor: `actas cmd`).
2. **The Claude Code skill.** It is now `skills/actas` (skill name `actas`).
   A `~/.claude/skills/ccenv` that links to a checkout's `skills/ccenv` points
   nowhere after the pull, and Claude Code drops the skill without a message.
   A copied one still looks for `ccenv-stop:` lines, which actas no longer
   prints. Replace it (doctor: `skill`):

   ```bash
   rm ~/.claude/skills/ccenv          # a link; rm -r if it is a copied directory
   ln -s <checkout>/skills/actas ~/.claude/skills/actas   # or copy the directory
   ```

3. **AGENTS.md blocks** you pasted from `ccenv instructions` also look for
   `ccenv-stop:`: replace them with the output of `actas instructions`.
   Refusals stay safe without this (the gate still refuses with exit 3 and
   says to show you the text and stop), but the old marker no longer matches.

### Optional, at your own pace

Run `actas doctor` after each:

1. Run `actas sync`, so synced files call `actas` instead of `ccenv`.
2. Move the directories, with no actas or ccenv session open (open sessions
   recorded the old path, and their agents would be refused with `override`):

   ```bash
   mv ~/.config/ccenv ~/.config/actas
   mv ~/.local/state/ccenv ~/.local/state/actas     # or $XDG_STATE_HOME/ccenv
   rm -rf ~/.cache/ccenv
   ```

   `actas doctor` prints these lines while the old directories are in use.
3. Rename variables you export yourself: `CCENV_ROOT` → `ACTAS_ROOT`,
   `CCENV_STATE` → `ACTAS_STATE`, `CCENV_AGENT` → `ACTAS_AGENT`,
   `CCENV_PATH_EXTRA` → `ACTAS_PATH_EXTRA`, and anything that reads `$CCENV`
   (a prompt, a script) → `$ACTAS`.
4. Replace `use ccenv` with `use actas` in `.envrc` files, and update your
   `~/.config/direnv/direnvrc` from `actas instructions --direnv`.
5. Secret refs you named after the tool, such as `keychain:ccenv/client-a`,
   keep working as they are; the name inside a ref is only a label.
6. Stop typing `ccenv`. The alias will be removed in a later major version.
