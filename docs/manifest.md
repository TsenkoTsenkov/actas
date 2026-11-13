# The actas manifest

> This describes the version 2 manifest, read by actas 2.0 (beta `2.0.0b1`; see the README's
> [Install](../README.md#install)). `actas --version` shows yours. actas 1.x reads only
> [v1 manifests](#v1-manifests); it finds no profiles in a version 2 file (`actas list` prints
> `no environments defined`).

One file, `~/.config/actas/environments.yaml` (or `$ACTAS_ROOT/environments.yaml`), says which
identity applies where: the model provider, cloud accounts, GitHub account, git identity and
any other variables. actas matches your working directory to a **profile** and applies it to one
process: Claude Code, Codex, Gemini or any command you run with `actas exec`.

The manifest holds names and references only. Secret values stay in a secret store
(sops, the macOS Keychain, 1Password, `gh`, or any command), and actas reads them when a session
starts.

> **The four rules**
>
> 1. **Later layers win, maps merge.** A layer is `defaults`, a role you `use:`, or the profile
>    itself, applied in that order. See [roles and `use:`](#roles-and-use).
> 2. **`null` means off: the tool sees no account.** `aws: null` gives the AWS CLI an empty config.
>    See [`null`, `"inherit"`, `inherit:` and `unset:`](#null-inherit-inherit-and-unset).
> 3. **Compose upward.** A profile is at least as privileged as anything it uses. See [levels](#levels).
> 4. **A secret is always `scheme:path`.** For example `sops:client-a/token` or `keychain:actas/gw`.
>    See [secret references](#secret-references).

Contents

- [A minimal manifest](#a-minimal-manifest)
- [When you have more than one identity per client](#when-you-have-more-than-one-identity-per-client)
- [Reference](#reference): [top-level keys](#top-level-keys), [profile and role keys](#profile-and-role-keys),
  [modules](#modules), [tools](#tools), [secret references](#secret-references),
  [directory matching](#directory-matching), [merge rules](#merge-rules),
  [the process environment](#the-process-environment), [validation](#validation),
  [files](#files-actas-uses)
- [v1 manifests](#v1-manifests)

See also: [recipes](recipes.md) for GitLab, npm, Docker, Terraform, other agents and more;
[`examples/environments.yaml`](../examples/environments.yaml) for a complete manifest;
[agents](agents.md) for the agent guardrails; [desktop](desktop.md) for Claude Desktop and IDEs.

---

## A minimal manifest

It is also in [examples/minimal.yaml](../examples/minimal.yaml).

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
    claude: { provider: gateway, base_url: "https://llm.client-a.example", token: "keychain:actas/client-a" }
```

What this does:

- In `~/code/personal` and below, sessions commit as `ada@example.com`, use the `gh` login
  `ada-personal` (read with `gh auth token --user ada-personal`, without switching gh's active
  account), and AWS profile `personal`.
- In `~/code/client-a`, the same for the client accounts, and Claude Code talks to the client's
  LLM gateway. The gateway token is read from the macOS Keychain (service `actas`, account
  `client-a`) only when Claude asks for it.

Try it:

```bash
actas list                          # every profile, its provider, AWS profile and GitHub account
cd ~/code/client-a && actas status  # which profile applies here, and why
actas secret set keychain:actas/client-a   # store the gateway token (you are prompted)
actas run                           # launch Claude Code as client-a
actas exec client-a -- aws s3 ls    # run any command as client-a
```

Everything except `version: 2` is optional, but you need at least one profile to run anything.
A profile with no `match` can still be used by name (`actas run client-a`).

---

## When you have more than one identity per client

Real setups have a read-only and a write identity for the same client, a shared gateway, a
production account that agents must not touch. The pieces below cover that. You do not need any
of them for the minimal manifest.

### Roles and `use:`

A **role** is a reusable piece of a profile. It is never matched or run on its own. A profile (or
another role) pulls roles in with `use:`.

```yaml
version: 2
roles:
  corp-gateway:
    claude: { provider: gateway, base_url: https://llm-gateway.corp.example, token: "sops:corp-gateway/token" }
  client-a-base:
    use: [corp-gateway]
    git:    { name: Ada Example, email: ada@client-a.example }
    github: { auth: "gh:ada-client-a" }
profiles:
  client-a-test:
    match: ~/code/client-a
    use: [client-a-base]
    aws: { profile: client-a-test-dev, region: eu-west-1 }
  client-a-prod:
    match: ~/code/client-a/infra/prod
    use: [client-a-base]
    aws: { profile: client-a-prod-readonly, region: eu-west-1 }
```

How layers combine:

- The order is `defaults`, then each entry of `use:` (expanded recursively, left to right), then
  the profile's own keys. Each layer appears once: with `A: use [B, C]` and `B`, `C` both using
  `D`, the order is `D, B, C, A`.
- **Maps merge key by key; for any single value, the later layer wins.** `client-a-test` above
  gets `git`, `github` and `claude` from the roles and its own `aws`.
- A profile can `use:` another profile. Its `match`, `use`, `description` and `agents_may_use`
  are not inherited.
- Profiles and roles share one namespace: a name can be one or the other, not both.
- A cycle (`a` uses `b` uses `a`) and chains deeper than 8 are errors.

Run `actas status <profile> --why` to see the chain and which layer set each value:

```text
chain     defaults -> gh-personal-ro -> aws-personal-ro -> no-pulumi -> personal -> aws-personal-power -> personal-power
          aws.profile = personal-poweruser (from aws-personal-power, was personal-readonly from aws-personal-ro)
          aws.region = eu-west-1 (from aws-personal-ro)
```

### Permission sets

A permission set is just a role that only carries agent rules (and optionally a `level`):

```yaml
version: 2
roles:
  no-pulumi: { permissions: { deny: ["Bash(pulumi up *)", "Bash(pulumi destroy *)"] } }
  no-apply:  { permissions: { deny: ["Bash(terraform apply *)", "Bash(terraform destroy *)"] } }
profiles:
  infra:
    match: ~/code/infra
    use: [no-pulumi, no-apply]       # both deny lists apply
```

`permissions.deny` and `permissions.ask` are **unioned** across layers, never replaced. They are
also **sticky**: a later layer cannot remove them, not even with `permissions: null`. That is an
error naming the layer that added the rule:

```text
actas: a: cannot replace permissions: it would drop permissions.deny (added by guard)
```

The rules use Claude Code's permission syntax and are written into Claude's settings (see
[permissions](#permissions)).

### Levels

Every profile has a level: `readonly`, `standard` (the default) or `power`. The level says how
much the profile's credentials can do, and picks a built-in set of agent rules.

| | readonly | standard | power |
|---|---|---|---|
| Edit local files | allowed | allowed | allowed |
| `git push`, `gh pr merge`, `gh release`, `glab mr merge` | denied | ask | allowed |
| Deploys (`terraform apply`, `kubectl apply`, `helm upgrade`, `pulumi up`, `cdk deploy`, `aws * delete-*`, …) | denied | ask | ask |
| Reading secrets (`actas secret`, `sops`, `gh auth token`, credential files, …) | denied | denied | denied |
| Switching profile (`actas exec`, `actas run`, `actas login`, `actas sync`) | ask | ask | ask |
| Gemini CLI `--approval-mode` | `default` | `default` | `auto_edit` |

"Readonly" means read-only toward remote systems and credentials. Local edits stay allowed in
every level. For Claude's plan mode, add `claude: { settings: { permissions: { defaultMode: plan } } }`.

The level is a label for credentials you chose, not a sandbox. A readonly profile is only
read-only if its credentials are: a read-only AWS role, a fine-grained read-only GitHub token.
`actas doctor` says so when a readonly profile uses a `gh:` login, which usually has full repo
scope.

**Compose upward.** A profile's level is the highest level in its chain (or `standard` if none is
set). A profile cannot declare a level lower than a layer it uses:

```text
actas: a: declares level readonly but uses a higher-level layer (compose upward: a profile is at least as privileged as what it uses)
```

So build a power profile on top of a readonly one, never the reverse:

```yaml
version: 2
roles:
  aws-personal-ro:    { level: readonly, aws: { profile: personal-readonly, region: eu-west-1 } }
  aws-personal-power: { level: power,    aws: { profile: personal-poweruser } }
profiles:
  personal:
    match: ~/code/personal
    use: [aws-personal-ro]                  # readonly
  personal-power:
    use: [personal, aws-personal-power]     # power: AWS becomes PowerUser, region stays
    confirm: true
```

### `confirm`

`confirm: true` means a human must type the profile name before `actas run` or `actas exec` uses
it, and agents are always refused. `confirm` is OR'd through the chain: if any layer sets it, the
profile has it. `actas sync` skips `confirm` profiles unless you pass `--include-confirm`.

`actas doctor` warns about a `power` profile without `confirm`.

### `agents_may_use`

An agent session started with `actas run` may run commands in other profiles with
`actas exec <profile> -- <cmd>` only if:

- the target is the session's own profile, or is listed in the session profile's `agents_may_use`
  (`"*"` lists every profile);
- the target's level is the same or lower than the session's;
- the target has no `confirm`.

Otherwise actas refuses with an `actas-stop:` line and a text for the agent to show you. You then
run the command yourself in a separate terminal.

```yaml
profiles:
  client-a-test:
    match: ~/code/client-a
    agents_may_use: [client-a-prod]   # client-a-prod is readonly: an agent here may read prod
```

`"*"` lets agents reach every profile, still only at the same or a lower level and never a
`confirm` profile. To give every profile the same list, put it in `defaults:`; a profile's own
`agents_may_use` replaces the default one (`agents_may_use: []` opts a profile out):

```yaml
defaults:
  agents_may_use: ["*"]     # an agent may push as client-b from a client-a session
profiles:
  client-a-prod:
    level: readonly
    agents_may_use: []      # but not from here
```

`agents_may_use` is not inherited through `use:`, and hops do not chain: a nested `actas run` an
agent starts keeps the outer session's list. The full gate is in [agents.md](agents.md).

### `null`, `"inherit"`, `inherit:` and `unset:`

actas clears identity variables your shell already has (for example a stale `AWS_PROFILE`), so a
session never runs as the wrong account by accident. These keys control that:

| You write | Effect |
|---|---|
| `aws: { … }` | The profile's AWS settings. Your shell's AWS variables are cleared. |
| *(no `aws` key anywhere in the chain)* | Nothing set. Your shell's AWS variables are still cleared, if the manifest uses `aws` anywhere. Likewise a variable some other profile sets in `secret_env`. |
| `aws: null` | **Off and isolated.** The AWS CLI gets an empty config, so it cannot fall back to your `[default]` profile. Same idea for `gcp`, `azure` and `github`. |
| `aws: inherit` | Off. Your shell's AWS variables pass through untouched. |
| `inherit: [AWS_PROFILE]` | Keep these variables from your shell even though a module owns them. |
| `unset: [DOCKER_HOST]` | Always remove these variables from the session. `unset` beats everything, including `inherit` and values a profile sets. |
| `env: { X: null }` or `secret_env: { X: null }` | Remove `X` from the session (like `unset`, for one variable). |

The empty home behind `null` (`homes/<module>/_off`) is shared by every profile that turns the
module off, so never log in inside it: actas moves a login it finds there aside (with a warning)
and `doctor` reports it.

`unset` is unioned and sticky across layers, like the deny lists. `inherit` is unioned.

`unset` wins over every value for that variable. `null` in `env` (or `secret_env`) removes the
variable even when another key sets it, for example a later layer's `secret_env`, but a later
layer's own `env:` value replaces the null as usual. Use them in the profiles that should not see
a variable, not in `defaults`.

### `fallback`

```yaml
fallback: scratch          # used when no profile matches the directory
profiles:
  scratch: { level: readonly }
```

Without `fallback`, running actas outside every `match` is an error that lists your profiles and
suggests a `match` entry for the directory (its git repository's top directory when it is in one
under your home). `actas status` there reports no profile (`"profile": null` with `--json`, plus a
`hint`) and exits 0; `status --why` and `--probe` need a profile and exit 1.

### Checking your work

- `actas status [profile] --why` shows the chain, the source of each value, the variables set,
  cleared and read from secrets, and the agent rules.
- `actas doctor` validates the manifest and lists problems by path. `actas doctor --strict` turns
  warnings into errors.
- `actas list --json` lists profiles and roles for scripts; `actas list --names` prints one
  profile name per line.

---

## Reference

### Top-level keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `version` | `1` or `2` | `1` | `2` for this format. A file without `version` (or with `1`) is read as a [v1 manifest](#v1-manifests). |
| `defaults` | profile body | `{}` | The first layer of every profile. It may not contain `match`, `use`, `description`, `level` or `confirm` ("put it in a role and `use:` it"). `agents_may_use` here is the list for every profile without its own. |
| `profiles` | map name → profile body | `{}` | Runnable identities, matched to directories. |
| `roles` | map name → profile body | `{}` | Reusable pieces, including permission sets. Never matched or run. `match` and `agents_may_use` are errors in a role. |
| `fallback` | profile name | none | The profile used when no `match` fits. |

`environments:`, `permission_sets:` and `modules:` are errors in a v2 file, each with a hint
(rename to `profiles:`, define a role, write a role from the [recipes](recipes.md)).

Names of profiles and roles: letters, digits, `.`, `_`, `-`, starting with a letter or digit.

### Profile and role keys

"Through `use:`" says how a key combines across layers. *Local* keys are never inherited.

| Key | Type | Default | Through `use:` | Meaning |
|---|---|---|---|---|
| `description` | string | — | local | Shown by `list` and `status`. |
| `match` | string or list | `[]` | local | Directory patterns ([matching](#directory-matching)). Profiles only. |
| `use` | string or list | `[]` | local | Roles or profiles applied before this body, in order. |
| `agents_may_use` | list of profile names, or `["*"]` | `[]` | local | Profiles an agent in this profile's session may `exec` ([above](#agents_may_use)). Profiles and `defaults:` only. |
| `level` | `readonly` / `standard` / `power` | `standard` | highest wins | Declared privilege ([levels](#levels)). |
| `confirm` | bool | `false` | any `true` wins | A human must type the profile name; agents are refused. |
| `tool` | `claude` / `codex` / `gemini` | `claude` | later wins | What `actas run` launches. `actas run -t TOOL` overrides it. |
| `permissions` | map | `{}` | per field | `deny`, `ask` (lists, unioned, sticky), `builtin` (bool). See [permissions](#permissions). |
| `inherit` | list of variable names | `[]` | unioned | Shell variables passed through even though a module owns them. |
| `unset` | list of variable names | `[]` | unioned, sticky | Always removed from the session. Wins over everything. |
| `env` | map VAR → string | `{}` | merged | Plain values. `null` removes the variable. Never secrets: `actas sync` writes these into settings files. |
| `secret_env` | map VAR → ref | `{}` | merged | Values read from a [secret reference](#secret-references) at launch, into the process environment only. Never written to a file. `null` removes the variable. |
| `aws`, `gcp`, `azure`, `github`, `git` | map, `inherit` or `null` | — | merged | [Modules](#modules). |
| `claude`, `codex`, `gemini` | map | — | merged | [Tools](#tools). |

Lists replace earlier lists (`claude.args`, `codex.args`, `claude.settings.permissions.allow`, …),
except `permissions.deny`, `permissions.ask`, `claude.settings.permissions.deny`,
`claude.settings.permissions.ask`, `inherit` and `unset`, which are unioned. `""` sets an empty
value; `null` removes the key.

#### permissions

| Key | Type | Default | Meaning |
|---|---|---|---|
| `deny` | list of Claude rules | `[]` | Added to the deny list. Unioned and sticky. |
| `ask` | list of Claude rules | `[]` | Added to the ask list. Unioned and sticky. |
| `builtin` | bool | `true` | `false` drops the level's built-in rules; only your own lists are rendered. The last layer that sets it wins. `doctor` reports which layer turned it off. |

Effective rules = the level's built-in rules (unless `builtin: false`) + every `deny`/`ask` in the
chain + `claude.settings.permissions` lists. They reach Claude Code through the settings file
`actas run` passes (`--settings`) and the `.claude/settings.local.json` that `actas sync` writes.
Codex and Gemini do not get these rules in this release; Gemini gets `--approval-mode` from the
level. The built-in secret rules also deny editing actas's own config and state directories, and
reading the age key and the credential files of every module the manifest uses (for example
`~/.aws/sso/cache`, `~/.config/gh/hosts.yml`, a Claude home's `.credentials.json`, Gemini's
`~/.gemini/oauth_creds.json`).

Rules are a seatbelt for an agent that follows instructions, not a security boundary. Narrowly
scoped credentials are the boundary. See [agents.md](agents.md).

### Modules

Each module is a map, the string `inherit` (off, shell values pass through) or `null` (off and
isolated). A **home** is either a name, which means a directory actas manages under
`~/.local/state/actas/homes/<module>/<name>` (created mode 0700 on first use), or an absolute or
`~/` path.

#### aws

| Key | Type | Effect |
|---|---|---|
| `profile` | string | `AWS_PROFILE`. Also the Claude setting `awsAuthRefresh` (`aws sso login --profile …`) when the AWS CLI is installed. |
| `region` | string | `AWS_REGION` and `AWS_DEFAULT_REGION`. |
| `config_file` | path | `AWS_CONFIG_FILE`. |
| `sso_session` | string | SSO logins (`actas login`, `run --login`, `aws-handoff --login`) run `aws sso login --sso-session <name>` instead of `--profile`. |

- Clears from your shell: `AWS_PROFILE AWS_DEFAULT_PROFILE AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_REGION AWS_DEFAULT_REGION`,
  the role and container credential overrides `AWS_ROLE_ARN AWS_ROLE_SESSION_NAME AWS_WEB_IDENTITY_TOKEN_FILE AWS_CONTAINER_CREDENTIALS_* AWS_CONTAINER_AUTHORIZATION_TOKEN*`
  (the SDKs read them before an SSO profile),
  plus `AWS_CONFIG_FILE AWS_SHARED_CREDENTIALS_FILE` when some layer uses `config_file` or `aws: null`.
- `aws: null`: `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` point at two empty files under
  `homes/aws/_off/`.
- In agent sessions: `AWS_PAGER=` (empty).
- `actas login [profile]` runs `aws sso login` for it; `actas status --probe` runs
  `aws sts get-caller-identity`.

#### gcp

| Key | Type | Effect |
|---|---|---|
| `configuration` | string | `CLOUDSDK_ACTIVE_CONFIG_NAME` |
| `project` | string | `CLOUDSDK_CORE_PROJECT`, `GOOGLE_CLOUD_PROJECT` |
| `region` | string | `CLOUDSDK_COMPUTE_REGION` |
| `impersonate` | service account email | `CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT`, `GOOGLE_IMPERSONATE_SERVICE_ACCOUNT` (not yet verified that gcloud honours the first one from the environment) |
| `home` | home | `CLOUDSDK_CONFIG`: a separate gcloud login and configuration set. |
| `adc_file` | path | `GOOGLE_APPLICATION_CREDENTIALS` |

- Also clears `GOOGLE_CREDENTIALS`, `GOOGLE_OAUTH_ACCESS_TOKEN`, and `CLOUDSDK_CORE_ACCOUNT`,
  `CLOUDSDK_AUTH_ACCESS_TOKEN_FILE`, `CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE` (they override the
  configuration's account).
- `gcp: null`: `CLOUDSDK_CONFIG` points at an empty directory.
- In agent sessions, when the profile has a gcp block: `CLOUDSDK_CORE_DISABLE_PROMPTS=1`.
- `actas login [profile] --module gcp [--adc]` runs `gcloud auth login` (and
  `gcloud auth application-default login`) with the profile's environment, so a `home` gets its
  own login.

#### azure

| Key | Type | Effect |
|---|---|---|
| `home` | home | `AZURE_CONFIG_DIR`: a separate `az` login. |
| `subscription` | string | `ARM_SUBSCRIPTION_ID` |
| `tenant` | string | `ARM_TENANT_ID` |
| `location` | string | `AZURE_DEFAULTS_LOCATION` |

- Also clears `ARM_CLIENT_ID ARM_CLIENT_SECRET ARM_USE_CLI ARM_USE_OIDC ARM_USE_MSI AZURE_DEFAULTS_GROUP`,
  `ARM_ACCESS_KEY ARM_CLIENT_CERTIFICATE_PATH ARM_CLIENT_CERTIFICATE_PASSWORD ARM_OIDC_TOKEN`, and the
  Azure SDK's service-principal variables `AZURE_CLIENT_ID AZURE_CLIENT_SECRET AZURE_TENANT_ID
  AZURE_SUBSCRIPTION_ID AZURE_CLIENT_CERTIFICATE_PATH AZURE_CLIENT_CERTIFICATE_PASSWORD
  AZURE_FEDERATED_TOKEN_FILE AZURE_USERNAME AZURE_PASSWORD`.
- Without `home`, `az` keeps using `~/.azure` and its current subscription; only tools that read
  `ARM_SUBSCRIPTION_ID` (Terraform) are pinned. `status --why` notes this.
- `azure: null`: `AZURE_CONFIG_DIR` points at an empty directory.
- In agent sessions, when the profile has an azure block: `AZURE_CORE_LOGIN_EXPERIENCE_V2=no`.
- `actas login [profile] --module azure` runs `az login [--tenant …]`, then
  `az account set --subscription …` only inside a `home` (never against `~/.azure`).

#### github

Set exactly one of `auth` or `home`.

| Key | Type | Effect |
|---|---|---|
| `auth` | ref | A token: `gh:<login>[@host]` (a login stored by `gh auth login`) or any other [ref](#secret-references), such as a fine-grained PAT in sops. |
| `home` | home | `GH_CONFIG_DIR`: a separate `gh` login, used as is. |
| `host` | hostname | Default `github.com`. Must agree with `@host` in a `gh:` ref; a `gh:<login>` without `@host` is the login on this host (`gh:ada` with `host: ghe.corp.example` reads `gh:ada@ghe.corp.example`). |

With `auth`:

- `GH_TOKEN` and `GITHUB_TOKEN` (for `github.com` and `*.ghe.com`), or `GH_ENTERPRISE_TOKEN` and
  `GITHUB_ENTERPRISE_TOKEN` (other hosts). These are secret: process environment only. Any host
  other than `github.com` also sets `GH_HOST`, so `gh` outside a repo targets it.
- git gets a credential helper for `https://<host>` that reads the same ref when git asks.
  Earlier helpers for that host (for example the macOS keychain) are reset for the session.
- **Fails closed:** `GH_CONFIG_DIR` points at an empty directory, so if the token is missing,
  `gh` reports "not logged in" instead of using whichever account is active in your keyring
  (not yet verified against every gh version).
- The cost: your gh aliases and config are not loaded in that session; use `home:` if you need
  them.
- The exception is your own direnv/shell output (`shell --no-secrets` outside an agent session,
  which `use actas` runs). It leaves the token out by design, so it leaves `GH_CONFIG_DIR` alone
  too and `gh` there uses its keyring login (`git push` uses the helper either way).

With `home`: `GH_CONFIG_DIR` (and `GH_HOST` for a non-github.com host), and git uses
`gh auth git-credential` from that home.

- Clears: `GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GH_HOST GH_CONFIG_DIR COPILOT_GITHUB_TOKEN`.
- `github: null`: empty `GH_CONFIG_DIR`, and git's credential helpers for `https://github.com` are
  reset with nothing after them (public clones still work; nothing authenticates).
- In agent sessions: `GH_PROMPT_DISABLED=1`, `GIT_TERMINAL_PROMPT=0`.
- `actas login [profile] --module github`: for `gh:` runs `gh auth login --web`, then switches
  gh's active account back to the one that was active before (gh makes a new login active); for a
  token ref, prints how to create a fine-grained PAT and store it.

The git helper keeps tokens out of files, but any process running as you can still ask git for
them (`git credential fill`). It is a convenience, not a boundary.

#### git

| Key | Type | Effect (as per-process git config) |
|---|---|---|
| `name`, `email` | string | `user.name`, `user.email` |
| `ssh_key` | path | `core.sshCommand = ssh -i '<key>' -o IdentitiesOnly=yes` |
| `signing.format` | string (`ssh`, `openpgp`, `x509`) | `gpg.format` |
| `signing.key` | string | `user.signingkey` |
| `signing.program` | string | `gpg.ssh.program` (format `ssh`) or `gpg.program` |
| `sign` | bool | `commit.gpgsign`, `tag.gpgsign` |
| `credentials` | map URL → `{username, token: ref}` | A credential helper per URL that reads the ref (username default `x-access-token`). For GitLab, Bitbucket, Azure DevOps, Gitea: see [recipes](recipes.md). |
| `config` | map key → string | Any other git config, applied last. `null` skips a key. |

All of it goes through `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n`, appended after
any your shell already has. `ACTAS_GIT_BASE` marks where actas's pairs start (0 in synced files,
`shell --format env` and `desktop --vscode`), so a nested `actas exec`/`run` replaces them,
including another profile's credential helper. actas never edits `~/.gitconfig`. `git: null` just sets no identity.

- Clears: `GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_SSH_COMMAND`.
  git ranks these above `user.name`/`user.email`/`core.sshCommand`, so another client's exports
  (a direnv `.envrc`, an outer session) would otherwise win. `inherit: [GIT_SSH_COMMAND]` keeps
  one; `git: inherit` keeps them all. (`GIT_SSH` is left alone: `core.sshCommand` beats it.)
- In agent sessions, when the profile has a git block: `GIT_TERMINAL_PROMPT=0`.

#### env and secret_env

```yaml
env:        { TF_IN_AUTOMATION: "1", KUBECONFIG: /home/ada/.kube/client-a.yaml }
secret_env: { NPM_TOKEN: "sops:client-a/npm", SENTRY_AUTH_TOKEN: "op://Work/Sentry/token" }
```

- `env` values are literal text: `~` and `$VARS` are **not** expanded, so write absolute paths.
  Numbers are converted to text; booleans are an error (quote `"true"`, `"yes"`, `"on"`).
  `actas sync` writes `env` into `.claude/settings.local.json`, so `doctor` warns when a name looks
  secret (`KEY`, `TOKEN`, `SECRET`, `AUTH`, `PASSWORD`, `HEADERS`).
- `secret_env` values must be [refs](#secret-references) (or `null`, which removes the variable). They are read at launch into the process
  environment only. A secret that cannot be read is a warning, and the variable is left unset
  (never your shell's value).
- Variable names: letters, digits and `_`, not starting with a digit.

### Tools

#### claude

| Key | Type | Default | Meaning |
|---|---|---|---|
| `provider` | `subscription` / `gateway` / `api-key` / `bedrock` / `vertex` | `subscription` | Where Claude Code sends requests. |
| `home` | home | none | `CLAUDE_CONFIG_DIR`: a separate claude.ai login, settings and history (a second subscription). Process environment only. |
| `base_url` | URL | — | gateway: required. bedrock: optional `ANTHROPIC_BEDROCK_BASE_URL`. |
| `token` | ref | — | gateway, api-key: required. Reaches Claude through `apiKeyHelper` (`actas secret --ref …`), not the environment. |
| `export_token` | bool | `false` | Also put the token in the process environment (`ANTHROPIC_AUTH_TOKEN`, or `ANTHROPIC_API_KEY` for api-key or `auth_header: x-api-key`). |
| `auth_header` | `bearer` / `x-api-key` | `bearer` | Which header the gateway expects (also used by `actas desktop --print-3p`). |
| `models` | map `opus`, `sonnet`, `haiku`, `default`, `subagent` | — | `ANTHROPIC_DEFAULT_OPUS_MODEL`, `…_SONNET_…`, `…_HAIKU_…`, `ANTHROPIC_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`. |
| `oauth_token` | ref | — | subscription: `CLAUDE_CODE_OAUTH_TOKEN`, process environment only. |
| `project` | string | `gcp.project` | vertex: `ANTHROPIC_VERTEX_PROJECT_ID`. |
| `region` | string | — | vertex: `CLOUD_ML_REGION` (required). |
| `bearer_token` | ref | — | bedrock: `AWS_BEARER_TOKEN_BEDROCK`, process environment only. |
| `args` | list | `[]` | Arguments always passed to `claude`, before the ones after `--`. |
| `settings` | map | `{}` | Raw Claude Code settings, merged last into the generated settings (`permissions` lists are unioned). `permissions.defaultMode: auto` or `bypassPermissions` is an error. |

Per provider:

| provider | Needs | Claude Code gets |
|---|---|---|
| `subscription` | nothing | Any provider variables from your shell cleared; uses the claude.ai login of the Claude home. |
| `gateway` | `base_url`, `token` | `ANTHROPIC_BASE_URL`, `apiKeyHelper`. |
| `api-key` | `token` | `apiKeyHelper` with the key (Anthropic API). |
| `bedrock` | `aws.region`, and `aws.profile` or `bearer_token` | `CLAUDE_CODE_USE_BEDROCK=1` plus the AWS module's variables and `awsAuthRefresh`. Pin `models` to IDs your account has enabled (actas warns when none are set). |
| `vertex` | `region`, and `project` or `gcp.project` | `CLAUDE_CODE_USE_VERTEX=1`, `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`. |

Foundry and other extras (custom headers, model discovery) are [recipes](recipes.md#claude-code-extras).
Secrets that reach the process environment (`oauth_token`, `bearer_token`, `export_token: true`,
`secret_env`) can be read by the agent with `env`; for those profiles consider
`env: { CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: "1" }`.

A new `home` starts empty (no user settings, plugins, MCP servers or `CLAUDE.md`). While the home
is still empty, actas prints `<home> has no login yet: run /login in the first claude session started there`.

#### codex

| Key | Type | Default | Meaning |
|---|---|---|---|
| `home` | home | none | `CODEX_HOME`: a separate Codex login and config. |
| `login` | `chatgpt` / `api` | `chatgpt` | How `actas login` signs Codex in. |
| `api_key` | ref | — | With `login: api`: `actas login` pipes the key to `codex login --with-api-key` on stdin. Never exported by actas, but Codex itself stores it in plain text in the home's `auth.json`, so `login: api` needs `home` (`actas login` refuses without one rather than overwrite the global `~/.codex`). |
| `args` | list | `[]` | Arguments always passed to `codex`. |

Codex support is environment level: actas sets `CODEX_HOME` and launches `codex`. It does not
write Codex's `config.toml` (a gateway is a [recipe](recipes.md#codex-through-a-gateway)), and
the level's agent rules are not rendered for Codex. Clears: `CODEX_HOME CODEX_API_KEY OPENAI_API_KEY OPENAI_BASE_URL CODEX_ACCESS_TOKEN`.

#### gemini

| Key | Type | Default | Meaning |
|---|---|---|---|
| `home` | home | none | `GEMINI_CLI_HOME`. |
| `auth` | `api-key` / `vertex` | — | `vertex`: `GOOGLE_GENAI_USE_VERTEXAI=true` (not yet verified for current Gemini CLI), `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`. |
| `api_key` | ref | — | `GEMINI_API_KEY`, process environment only (visible to the agent; prefer `vertex` with the gcp module). |
| `project` | string | `gcp.project` | vertex project. |
| `location` | string | — | vertex location. |
| `args` | list | `[]` | Arguments always passed to `gemini`. |

`actas run` launches `gemini --approval-mode <mode>` with the mode from the [level](#levels).
Clears: `GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_USE_VERTEXAI GOOGLE_GEMINI_BASE_URL GOOGLE_CLOUD_LOCATION GEMINI_CLI_HOME`.

Any other CLI or agent runs with `actas exec <profile> -- <command>` ([recipes](recipes.md#other-agents)).

### Secret references

A ref names its scheme. A bare value where a ref is expected is an error that suggests one.

| Scheme | Form | Read with | Write with `actas secret set REF` |
|---|---|---|---|
| sops | `sops:<group>/<key>` | `sops -d` of `~/.config/actas/secrets.enc.yaml`, key `["group"]["key"]`; one decrypt per launch | yes (value prompted, or piped on stdin; handed to sops on stdin when it supports `--value-stdin`). The first write creates `secrets.enc.yaml`, which needs `.sops.yaml` (see `actas init`) |
| keychain (macOS) | `keychain:<service>/<account>` | `security find-generic-password -s SERVICE -a ACCOUNT -w` | yes (`security` prompts for the value) |
| 1Password | `op://<vault>/<item>/<field>` | `op read REF` | no |
| gh | `gh:<login>` or `gh:<login>@<host>` | `gh auth token --user LOGIN --hostname HOST` | no (`gh auth login`) |
| command | `{cmd: [argv…]}` or `'cmd:["argv", …]'` | runs the command directly, no shell; stdout is the value | no |

Grammar:

```text
sops      sops:NAME/NAME                 NAME = [A-Za-z0-9][A-Za-z0-9_.-]*
keychain  keychain:PART/PART             PART = 1-128 of [A-Za-z0-9 ._@+-], not starting with "-"
op        op://BODY                      BODY = [A-Za-z0-9 _./?=-]+ ("$" is rejected)
gh        gh:LOGIN[@HOST]                LOGIN = [A-Za-z0-9][A-Za-z0-9_-]{0,38} (EMU logins may contain "_")
cmd       cmd:JSON-ARRAY-OF-STRINGS      or, in YAML, {cmd: [argv...]}
```

Details:

- Exactly one trailing newline is stripped from every value; nothing else.
- Reads have a 30 s timeout and never use a shell.
- `keychain:` works on macOS only; elsewhere use a `cmd:` ref with `secret-tool`
  ([recipe](recipes.md#secret-stores-through-cmd)).
- `cmd:` commands run with the environment actas itself started with, not the profile's, so put
  everything the command needs (addresses, project names) in its argv. `actas doctor` lists every
  `cmd:` ref.
- For 1Password with several accounts, export `OP_ACCOUNT` in the shell you start actas from, or
  use a `cmd:` ref that names the account:
  `{cmd: ["op", "read", "--account", "work", "op://Work/Sentry/token"]}`. A profile's `env:` does
  not reach refs read at launch (it does reach helpers that run later in the session).
- `actas secret --ref REF` prints a value (this is what helpers call). `actas secret ENV KEY`
  is the v1 form for `sops:ENV/KEY` and still works.

### Directory matching

- Patterns are expanded (`~`, `$VARS`), then a directory matches when it equals the pattern, is
  below it, matches it as a glob (`*` also crosses `/`), or is below a directory that matches the
  glob (`~/code/*/infra` covers `~/code/a/infra/modules`). `~/code/x/*` does not match
  `~/code/x` itself; plain `~/code/x` covers it and everything below.
- **The longest matching pattern wins**; on a tie, the profile and pattern listed first.
- Only when nothing matches: actas retries with your shell's logical `$PWD` (symlinked
  directories), then case-insensitively on macOS, then uses `fallback:`.
- `actas --cwd DIR …` resolves as if you were in `DIR`; `actas status` shows `matched_by`.

### Merge rules

In order: `defaults`, the linearized `use:` chain, the profile.

| Value | Rule |
|---|---|
| map | merged key by key, recursively |
| scalar, ordinary list | the later layer replaces it |
| `permissions.deny`, `permissions.ask`, `claude.settings.permissions.deny`, `claude.settings.permissions.ask`, `unset` | unioned; a later layer cannot remove them (an error, also through `null` or by replacing the parent map, such as `claude.settings: null`) |
| `inherit` | unioned |
| `null` | removes the key; on a module, turns it off and isolates it; in `env`, removes the variable |
| `"inherit"` (module) | turns the module off and lets the shell's variables through |
| `level` | the highest in the chain; a lower declared level is an error |
| `confirm` | true if any layer says so |
| `match`, `use`, `description` | taken from the profile itself only |
| `agents_may_use` | the profile's own list, else the one in `defaults:` (never from `use:`) |

### The process environment

What a session gets, in order of precedence (later wins): `aws`, `gcp`, `azure`, `github`, `git`,
`claude`, `codex`, `gemini`, `env`, `secret_env`; then `unset` and `null` remove variables; then
actas adds `ACTAS` (profile name), `ACTAS_LEVEL`, and, for agent tools (`run`, or `exec` of a
command named like a known agent CLI such as `aider`), `ACTAS_AGENT`. `actas run`
also sets `ACTAS_SESSION`. `doctor` warns when two modules set the same variable to different
values.

**What is cleared from your shell:** the variables owned by every module and tool your manifest
mentions anywhere (in any profile, role or `defaults`), unless the profile sets them, passes them
through with `inherit`, or turns the module to `inherit`. `claude` counts as mentioned whenever a
profile runs Claude (the default). Variables of modules you never mention are left alone (a
manifest without `codex` does not touch `OPENAI_API_KEY`). `actas run` prints
`cleared from your shell: …` when it removed something non-empty, and `actas doctor` lists what a
run in the current directory would clear.

Every variable the manifest names in some layer's `secret_env` is cleared the same way:
a profile that does not set `NPM_TOKEN` drops the `NPM_TOKEN` another profile's session or
`actas shell` left in your shell. Keep one with `inherit: [NPM_TOKEN]`. Plain `env:` names are
not cleared for other profiles (they are not secrets, and a `SSH_AUTH_SOCK` or `KUBECONFIG` one
profile sets must not vanish from every other session); a profile that must not see one lists it
in `unset:`. Session basics (`PATH`, `HOME`, `SSH_AUTH_SOCK`, `DISPLAY`, `EDITOR`, `VISUAL`,
`TERM`, `TERM_*`, `COLORTERM`, `LC_*`, `XDG_*`, …) are never cleared this way. Variables the manifest
never names (`GITLAB_TOKEN` exported in your shell profile, …) are left alone; add them to
`unset:` in profiles that must not see them.

### Validation

- Unknown keys are warnings with a suggestion (`unknown key profiles.x.secrets_env (did you mean secret_env?)`).
- Wrong types are errors with the path (`profiles.client-a.aws.profile: expected a string, got bool`).
- Lists of rules or names must be lists; a bare string is an error.
- Requirements that need the merged profile (a gateway without `base_url`, bedrock without
  `aws.region`, `github` with both `auth` and `home`) are checked when the profile is used, and by
  `doctor`.
- `doctor` also reports, for example: `level: power` without `confirm`, a readonly profile with a
  `gh:` login, `agents_may_use` entries that would always be refused, `confirm` profiles with
  `match` (sync skips them), conflicting modules, secret-looking `env` names, `cmd:` refs,
  profiles that nothing can reach, profiles with no `aws` when `~/.aws/config` or
  `~/.aws/credentials` has a `[default]` profile (add `aws: null`), `codex.login: api` without
  `codex.home` (a warning), `permissions.builtin: false` (an info line naming the layer), a login
  found in an `_off` home (an error), a config directory readable by others, and, when the
  manifest uses Gemini, `.env` files Gemini CLI would load.
- `doctor` checks what the manifest names against this machine, without reading a secret or
  going over the network: a `gh:` account that `gh auth status` does not list, an `aws.profile`
  in neither the AWS config file (the profile's own `config_file`, if set) nor the credentials
  file, a missing tool for a ref in use (`sops`, `secrets.enc.yaml` and an age key for `sops:`,
  `op` for `op://`, macOS `security` for `keychain:`, a `cmd:` program) or for `tool: codex` /
  `gemini`, and a git identity at a reserved example domain (`example.com`, `*.example`) or the
  name `Your Name`. All are warnings, so `doctor --strict` fails on them.
- The manifest may be YAML (needs PyYAML or `yq`) or JSON (no dependency).

### Files actas uses

| What | Where |
|---|---|
| Manifest | `~/.config/actas/environments.yaml` (`$ACTAS_ROOT` overrides the directory) |
| sops secrets and config | `~/.config/actas/secrets.enc.yaml`, `~/.config/actas/.sops.yaml` |
| Managed homes | `~/.local/state/actas/homes/<module>/<name>` (`$ACTAS_STATE`, or `$XDG_STATE_HOME/actas`) |
| Session records, sync ledger, audit log | `~/.local/state/actas/sessions/`, `sync-ledger.json`, `audit.jsonl`; a copy of each session record also goes under your passwd home's `~/.local/state/actas/sessions/`, which no variable moves |
| Generated Claude settings | `~/.cache/actas/<profile>.<hash>.settings.json` (`$XDG_CACHE_HOME`), pruned after 7 days |

Everything actas writes is mode 0600 in 0700 directories. Secret values are never written to any
of these files.

Set `ACTAS_ROOT` / `ACTAS_STATE` per command, or start agents with `actas run`. Exported globally
to a non-default place, they make agents without an `actas run` session (bare `claude`, IDE,
Desktop) refuse every `actas exec` with `reason=override` (see
[docs/agents.md, The gate](agents.md#the-gate)). A value equal to the default place is fine.

---

## v1 manifests

A file without `version: 2` is a v1 manifest. It keeps working unchanged: actas translates it on
load and runs it on the same engine.

| v1 | v2 equivalent |
|---|---|
| `environments:` | `profiles:` |
| `provider: subscription \| gateway` | `claude.provider` |
| `gateway.base_url` | `claude.base_url` |
| `gateway.secret` + `gateway.secret_from` | `claude.token: "sops:<secret_from or env>/<secret or token>"` |
| `claude_args` | `claude.args` (a string is split like a shell would, with a warning) |
| `github.account: A` | `github.auth: "gh:A"` |
| `github.name`, `github.email` | `git.name`, `git.email` |
| `secret_env.VAR: key` | `secret_env.VAR: "sops:<env>/<key>"` |
| `secret_env.GH_TOKEN` | `github.auth: "sops:<env>/<key>"` (git gets a helper for it) |
| `defaults:` (shallow: a block in the environment replaces the whole default block) | `defaults:` (deep merge) |
| `agents_may_use` (in an environment or in `defaults:`) | `agents_may_use` (the one v2 key a v1 file accepts; it only affects agents) |

For a v1 file, actas keeps v1 behaviour where v2 would differ:

- no built-in agent rules are written into Claude's settings;
- the gateway token is still exported (`ANTHROPIC_AUTH_TOKEN`);
- `gh` does not fail closed (no empty `GH_CONFIG_DIR`);
- only the variables v1 already managed are cleared from your shell (the Anthropic/Claude
  provider variables; AWS variables only for an environment with an `aws:` block; `GH_TOKEN` and
  `GITHUB_TOKEN` only with a GitHub account or `secret_env.GH_TOKEN`), not `CLAUDE_CONFIG_DIR`,
  `OPENAI_API_KEY`, `GOOGLE_*` or `ARM_*`. An environment without `aws:` keeps your shell's
  `AWS_PROFILE`, as v1 did. `actas migrate` writes `aws: "inherit"` for such an environment
  (v1's behaviour) and suggests `aws: null`, which isolates it.

A v1 file that uses v2 keys (`roles`, `use`, `level`, a `claude:` block, …) is an error that says
to add `version: 2`. The one exception is `agents_may_use`, which a v1 file accepts in
`defaults:` or in an environment. A v2 file with v1 keys (`provider`, `gateway`, `github.account`, …) is an
error that points to `actas migrate`.

**Converting:** `actas migrate` prints your manifest as v2. Every secret keeps its v1 sops path
(`sops:<environment>/<key>`), so `secrets.enc.yaml` needs no change, and actas never reads or
edits it while migrating. The output keeps `claude.export_token: true` where v1 exported the
token; stderr lists what changes once you switch (built-in agent rules, wider clearing, GitHub
failing closed, boolean `env` values becoming errors) and suggests roles for repeated blocks.
`actas migrate --write` replaces the manifest and keeps the old file as
`~/.config/actas/environments.v1.yaml`. Comments are not carried over.

[`examples/environments.v1.yaml`](../examples/environments.v1.yaml) is the v1 example;
[`examples/environments.yaml`](../examples/environments.yaml) is the v2 one.
