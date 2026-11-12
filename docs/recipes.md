# Recipes

ccenv has built-in modules for AWS, Google Cloud, Azure, GitHub and git, and tool blocks for
Claude Code, Codex and Gemini. Everything else is a **recipe**: a role that sets environment
variables, reads secrets, removes variables or adds git credentials. Copy the role into your
manifest's `roles:`, rename it, and `use:` it from a profile.

```yaml
roles:
  npm-client-a:
    secret_env: { NPM_TOKEN: "sops:client-a/npm" }
profiles:
  client-a:
    match: ~/code/client-a
    use: [npm-client-a]
```

What a recipe can use (details in [manifest.md](manifest.md)):

| Key | For |
|---|---|
| `env` | Plain values: hosts, account IDs, file paths. **Literal text: write absolute paths** (`~` and `$HOME` are not expanded). `ccenv sync` writes these into Claude's settings, so never put secrets here. |
| `secret_env` | Tokens and passwords, as [refs](manifest.md#secret-references) (`sops:`, `keychain:`, `op://`, `gh:`, `cmd:`). Process environment only. |
| `unset` | Variables the session must not inherit from your shell. |
| `git.credentials` | A git credential helper per URL that reads a ref when git asks. |
| `permissions` | Extra agent rules (Claude Code syntax). |

Two things to know:

- ccenv clears the variables its modules own, plus every variable any profile or role names in
  `secret_env` (so a role's `NPM_TOKEN` never leaks into another profile's session; keep it with
  `inherit: [NPM_TOKEN]`). Variables the manifest never names, such as a `GITLAB_TOKEN` exported
  in your shell profile, reach every session unless the profile sets them or lists them in
  `unset:` ([details](manifest.md#the-process-environment)).
- Secrets in `secret_env` are in the process environment, so an agent can read them with `env`.
  Prefer short-lived or narrowly scoped tokens, and see
  [Claude Code extras](#claude-code-extras) for `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`.

The variable names below come from each tool's documentation. ccenv passes them through and does
not test the tools themselves; check your tool's docs if something does not take effect.

Contents: [git hosts](#git-hosts) ·
[package registries](#package-registries) · [containers and Kubernetes](#containers-and-kubernetes) ·
[infrastructure and platforms](#infrastructure-and-platforms) · [databases](#databases) ·
[Claude Code extras](#claude-code-extras) · [secret stores through `cmd:`](#secret-stores-through-cmd) ·
[other agents](#other-agents) · [Codex through a gateway](#codex-through-a-gateway) ·
[read-only by default, power on demand](#read-only-by-default-power-on-demand) ·
[extra deny rules](#extra-deny-rules) · [stronger isolation](#stronger-isolation-devcontainer-or-vm) ·
[direnv, mise and AWS credential tools](#direnv-mise-and-aws-credential-tools)

---

## Git hosts

GitHub has a built-in module (`github:`). For other hosts, `git.credentials` adds a credential
helper for a URL. It resets any earlier helper for that URL (for example the macOS keychain), then
reads the ref each time git needs it. The token never lands in a file.

### GitLab

```yaml
roles:
  gitlab-client-c:
    env: { GITLAB_HOST: gitlab.com }                         # or your self-managed host
    secret_env: { GITLAB_TOKEN: "sops:client-c/gitlab" }     # for glab and API calls
    unset: [GITLAB_ACCESS_TOKEN, OAUTH_TOKEN]                # other names glab would read
    git:
      credentials:
        "https://gitlab.com": { username: oauth2, token: "sops:client-c/gitlab" }
```

- Read-only token scopes: `read_api` + `read_repository`. glab itself needs `api` +
  `write_repository`.
- Prefer a separate glab login over a token? Point glab at its own config directory instead:
  `env: { GLAB_CONFIG_DIR: /home/ada/.config/glab-client-c }` (absolute path), then run
  `ccenv exec <profile> -- glab auth login` once.
- The built-in push rules already cover `glab mr merge` (deny in readonly, ask in standard, no
  rule in power; see [levels](manifest.md#levels)).

### Bitbucket Cloud

```yaml
roles:
  bitbucket-client-d:
    git:
      credentials:
        "https://bitbucket.org": { username: x-bitbucket-api-token-auth, token: "sops:client-d/bitbucket" }
```

Create an API token with `read:repository:bitbucket` (add `write:repository:bitbucket` to push).
The username can also be your Bitbucket username.

### Azure DevOps

```yaml
roles:
  ado-client-e:
    secret_env: { AZURE_DEVOPS_EXT_PAT: "sops:client-e/ado_pat" }   # for `az devops` / `az repos`
    git:
      credentials:
        "https://dev.azure.com": { username: pat, token: "sops:client-e/ado_pat" }
```

Combine it with an `azure:` block that has a `home`, so `az` itself also logs in per client.

### Gitea, Forgejo and other HTTPS hosts

The same pattern works for any host that accepts a token over HTTPS:

```yaml
roles:
  gitea-client-f:
    git:
      credentials:
        "https://git.client-f.example": { username: ada, token: "keychain:ccenv/client-f-gitea" }
```

For GitHub Enterprise Server, use the built-in module: `github: { auth: "gh:ada@github.client-f.example" }`.

---

## Package registries

### npm (and pnpm)

```yaml
roles:
  npm-client-a:
    secret_env:
      NPM_TOKEN: "sops:client-a/npm"                          # registry.npmjs.org
      GITHUB_PACKAGES_TOKEN: "sops:client-a/github_packages"  # npm.pkg.github.com (read:packages)
```

In the project's `.npmrc` (safe to commit, it holds no secret):

```ini
//registry.npmjs.org/:_authToken=${NPM_TOKEN}
@client-a:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${GITHUB_PACKAGES_TOKEN}
```

### uv and pip

uv reads index credentials from `UV_INDEX_<NAME>_USERNAME` / `_PASSWORD`, where `<NAME>` is the
index name from `pyproject.toml` or `uv.toml`, upper-cased, with non-alphanumerics turned into `_`
(index `internal-proxy` → `UV_INDEX_INTERNAL_PROXY_PASSWORD`).

```yaml
roles:
  pypi-client-a:
    env: { UV_INDEX_INTERNAL_PROXY_USERNAME: ada }
    secret_env:
      UV_INDEX_INTERNAL_PROXY_PASSWORD: "sops:client-a/pypi"
      PIP_INDEX_URL: "sops:client-a/pip_index_url"     # pip: the whole URL, credentials included
    unset: [PIP_EXTRA_INDEX_URL]
```

pip has no separate password variable, so its URL (with credentials) is a secret.

### cargo

```yaml
roles:
  cargo-client-a:
    secret_env:
      CARGO_REGISTRIES_INTERNAL_TOKEN: "sops:client-a/cargo"   # registry "internal" in .cargo/config.toml
      CARGO_REGISTRY_TOKEN: "sops:personal/crates_io"          # crates.io
```

An empty `CARGO_*_TOKEN` does not override a token in cargo's config; use `unset:` to remove one
your shell exports.

---

## Containers and Kubernetes

### Docker

```yaml
roles:
  docker-client-a:
    env:
      DOCKER_CONTEXT: client-a                      # created once with `docker context create`
      DOCKER_CONFIG: /home/ada/.docker-client-a     # separate registry logins (absolute path)
    unset: [DOCKER_HOST]                            # DOCKER_HOST would override the context
```

With a credential store such as `osxkeychain`, logins are keyed by registry URL, so two
`DOCKER_CONFIG` directories may still share one login for the same registry (not yet verified).

### Kubernetes and Helm

Keep one kubeconfig file per client, using exec-plugin auth (`aws eks get-token`,
`gke-gcloud-auth-plugin`, `kubelogin`) rather than static tokens. The plugins inherit the
profile's `AWS_PROFILE`, `CLOUDSDK_CONFIG` or `AZURE_CONFIG_DIR`.

```yaml
roles:
  kube-client-a:
    env:
      KUBECONFIG: /home/ada/.kube/client-a.yaml     # absolute path
      HELM_KUBECONTEXT: client-a-test
```

The built-in deploy rules already cover `kubectl apply/delete` and `helm install/upgrade/uninstall`
(ask in standard and power, deny in readonly; see [levels](manifest.md#levels)).
Avoid `kubectl config use-context` in a shared kubeconfig: it changes every session using that file.

---

## Infrastructure and platforms

### Terraform and OpenTofu (HCP Terraform / Terraform Cloud)

```yaml
roles:
  tfc-client-a:
    secret_env: { TF_TOKEN_app_terraform_io: "sops:client-a/tfc" }   # host app.terraform.io
    env: { TF_CLI_CONFIG_FILE: /home/ada/.config/terraform/client-a.tfrc }
```

The variable name is `TF_TOKEN_` plus the host with `.` turned into `_` (and `-` into `__`).
Cloud providers read their own variables, which the `aws`, `gcp` and `azure` modules set
(`ARM_SUBSCRIPTION_ID` for azurerm).

### HashiCorp Vault / OpenBao

```yaml
roles:
  vault-client-a:
    env: { VAULT_ADDR: "https://vault.client-a.example", VAULT_NAMESPACE: admin }
    secret_env: { VAULT_TOKEN: "sops:client-a/vault_token" }
```

Without `VAULT_TOKEN`, `vault login` stores the token in `~/.vault-token`, which every session
shares. To read secrets *from* Vault into other variables, see
[secret stores through `cmd:`](#secret-stores-through-cmd).

### Pulumi

```yaml
roles:
  pulumi-client-a:
    secret_env: { PULUMI_ACCESS_TOKEN: "sops:client-a/pulumi" }
    env: { PULUMI_HOME: /home/ada/.pulumi-client-a }      # optional: separate plugins and logins
```

Use `PULUMI_BACKEND_URL` for a self-managed backend. The built-in deploy rules already cover
`pulumi up` and `pulumi destroy` (ask in standard and power, deny in readonly; see
[levels](manifest.md#levels)).

### Cloudflare (wrangler)

```yaml
roles:
  cloudflare-client-a:
    env: { CLOUDFLARE_ACCOUNT_ID: 0123456789abcdef0123456789abcdef }
    secret_env: { CLOUDFLARE_API_TOKEN: "sops:client-a/cloudflare" }
    unset: [CLOUDFLARE_API_KEY, CLOUDFLARE_EMAIL]      # legacy global key
```

### Vercel

```yaml
roles:
  vercel-client-a:
    env: { VERCEL_ORG_ID: team_example123, VERCEL_PROJECT_ID: prj_example456 }
    secret_env: { VERCEL_TOKEN: "sops:client-a/vercel" }
```

### Fly.io

```yaml
roles:
  fly-client-a:
    secret_env: { FLY_API_TOKEN: "sops:client-a/fly" }
    unset: [FLY_ACCESS_TOKEN]       # an alias that wins over FLY_API_TOKEN if both are set
```

Create a scoped, expiring token: `fly tokens create readonly -o <org> -x 720h` (without `-x` the
token lasts for years).

### Sentry

```yaml
roles:
  sentry-client-a:
    env: { SENTRY_ORG: client-a, SENTRY_URL: "https://sentry.client-a.example/" }   # URL: self-hosted only
    secret_env: { SENTRY_AUTH_TOKEN: "sops:client-a/sentry" }
```

`sentry-cli` also reads `~/.sentryclirc` and any `.sentryclirc` up the tree. `SENTRY_AUTH_TOKEN`
from the environment takes precedence, but a token in those files is used by every profile that
does not set one, so keep tokens out of them.

---

## Databases

### PostgreSQL (psql, pg_dump and libpq drivers)

```yaml
roles:
  pg-client-a-ro:
    env:
      PGSERVICEFILE: /home/ada/.config/pg/client-a.conf   # [client-a-ro] host=… user=readonly_role
      PGSERVICE: client-a-ro
      PGPASSFILE: /home/ada/.config/pg/client-a.pgpass    # mode 0600
    unset: [PGPASSWORD, PGUSER, PGHOST, PGDATABASE]
```

Keep one service per role (`client-a-ro`, `client-a-rw`), and put the read-only one in readonly
profiles. Better still, use IAM database tokens (`aws rds generate-db-auth-token`) or a cloud SQL
connector and keep passwords out of files entirely.

---

## Claude Code extras

Settings that have no `claude:` key go through `env` (non-secret) or `secret_env` (secret).

```yaml
roles:
  claude-gateway-extras:
    env:
      ANTHROPIC_CUSTOM_HEADERS: "X-Team: platform"       # "Name: Value", one header per line
      CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: "1"
      CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY: "1"
  claude-scrub-subprocess-env:
    env: { CLAUDE_CODE_SUBPROCESS_ENV_SCRUB: "1" }
```

- `ccenv doctor` warns about `ANTHROPIC_CUSTOM_HEADERS` in `env` because the name contains
  "HEADERS": that is expected for non-secret headers. If any header carries a secret, move the
  variable to `secret_env` with a ref holding the whole header text.
- `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1` strips credentials from the environment of Claude's Bash
  tool, hooks and stdio MCP servers, but leaves GitHub tokens. Use it for profiles that put
  secrets in the process environment (`secret_env`, `oauth_token`, `bearer_token`,
  `export_token`). Tools run by the agent then lack those secrets too.
- `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1` helps when a gateway rejects `anthropic-beta`
  headers; `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1` fills `/model` from the gateway's
  `/v1/models`. See Claude Code's
  [environment variable reference](https://code.claude.com/docs/en/env-vars).

### Microsoft Foundry

```yaml
roles:
  claude-foundry:
    env:
      CLAUDE_CODE_USE_FOUNDRY: "1"
      ANTHROPIC_FOUNDRY_RESOURCE: my-foundry-resource
    secret_env:
      ANTHROPIC_FOUNDRY_API_KEY: "sops:client-g/foundry"   # or ANTHROPIC_FOUNDRY_AUTH_TOKEN
```

Leave `claude.provider` at its default (`subscription`); the `env` value of
`CLAUDE_CODE_USE_FOUNDRY` replaces the blank ccenv writes for it. The key is in the process
environment. `ccenv list`, `status` and the run badge show such a profile as `foundry`, with
Remote Control off (a truthy `CLAUDE_CODE_USE_*` in `env` names the provider).

---

## Secret stores through `cmd:`

A `cmd:` ref runs a command (no shell) and uses its output, minus one trailing newline. Write it as
a YAML map, `{cmd: [program, arg, …]}`. The command runs with the environment ccenv was started
from, not the profile's, so put addresses and project names in its arguments. `ccenv doctor`
lists every `cmd:` ref.

```yaml
roles:
  secrets-examples:
    secret_env:
      # Linux Secret Service (GNOME Keyring, KeePassXC)
      NPM_TOKEN: { cmd: ["secret-tool", "lookup", "service", "ccenv", "account", "client-a-npm"] }
      # pass: the whole entry (one trailing newline stripped), so keep the token as its only line;
      # gopass: `gopass show -o` prints only the password
      SENTRY_AUTH_TOKEN: { cmd: ["pass", "show", "client-a/sentry"] }
      VAULT_TOKEN: { cmd: ["gopass", "show", "-o", "client-a/vault"] }
      # Bitwarden: needs an unlocked session (BW_SESSION in the shell you start ccenv from); use item IDs
      FLY_API_TOKEN: { cmd: ["bw", "get", "password", "00000000-0000-0000-0000-000000000000"] }
      # HashiCorp Vault KV: pass the address explicitly
      PULUMI_ACCESS_TOKEN: { cmd: ["vault", "kv", "get", "-address=https://vault.client-a.example", "-mount=secret", "-field=token", "client-a/pulumi"] }
      # Doppler: pass project and config explicitly
      VERCEL_TOKEN: { cmd: ["doppler", "secrets", "get", "VERCEL_TOKEN", "--plain", "-p", "client-a", "-c", "prd"] }
```

To store values in these tools, use the tool itself; `ccenv secret set` writes only `sops:` and
`keychain:` refs. On Linux, `secret-tool store --label=ccenv service ccenv account client-a-npm`
reads the value from stdin.

---

## Other agents

Any agent runs with a profile's identity through `ccenv exec`:

```bash
ccenv exec client-a -- aider
```

ccenv sets the cloud, GitHub and git identity; the variables below select each agent's own login,
model provider or config. Put them in a role for that profile.

| Agent | Command | Its own login / config | Key or gateway variables |
|---|---|---|---|
| Cursor CLI | `agent` | `CURSOR_CONFIG_DIR` | `CURSOR_API_KEY` |
| GitHub Copilot CLI | `copilot` | `COPILOT_HOME` | `COPILOT_GITHUB_TOKEN`, else `GH_TOKEN`/`GITHUB_TOKEN` from the `github` module |
| Aider | `aider` | none (stateless) | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AIDER_OPENAI_API_BASE` |
| opencode | `opencode` | `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG` | the provider's variables (`AWS_PROFILE`, `GOOGLE_CLOUD_PROJECT`, …) |
| Goose | `goose` | `GOOSE_PATH_ROOT` | `GOOSE_PROVIDER`, `GOOSE_PROVIDER__HOST`, `GOOSE_PROVIDER__API_KEY` |
| Amp | `amp` | — | `AMP_API_KEY`, `AMP_URL` |
| Kiro CLI | `kiro-cli` | — | `KIRO_API_KEY` (an existing `kiro-cli` login wins over it) |

Example:

```yaml
roles:
  goose-client-a:
    env:
      GOOSE_PATH_ROOT: /home/ada/.goose-client-a           # separate config, data and state
      GOOSE_PROVIDER: anthropic
      GOOSE_PROVIDER__HOST: "https://llm-gateway.corp.example"
    secret_env: { GOOSE_PROVIDER__API_KEY: "sops:corp-gateway/token" }
  copilot-client-a:
    env: { COPILOT_HOME: /home/ada/.copilot-client-a }
```

Notes:

- **Copilot CLI** follows the `github` module's `GH_TOKEN` automatically (it reads
  `COPILOT_GITHUB_TOKEN`, then `GH_TOKEN`, then `GITHUB_TOKEN`). Classic `ghp_` tokens are not
  supported, and a fine-grained PAT needs the "Copilot Requests" permission; otherwise set
  `COPILOT_GITHUB_TOKEN` separately in `secret_env`.
- **Aider** reads `ANTHROPIC_API_KEY`, which Claude Code also reads. Give Aider its own profile
  (it can `use:` the client's base role) so a Claude session in the same profile does not switch
  to that key. Aider also loads `.env` files from your home, the git root and the working
  directory, which can override what ccenv sets.
- **Agent detection.** `ccenv exec client-a -- aider` (or `copilot`, `amp`, `cursor-agent`,
  `opencode`, `goose`, `kiro-cli`) sets `CCENV_AGENT` for the tool it starts, so the `ccenv`
  commands it runs are treated as agent commands. Started any other way, script-based CLIs (Aider
  runs as `python`, npm-installed ones as `node`) and Cursor's `agent` are not recognised, so mark
  the launch yourself, never in a profile's `env:`
  ([why](agents.md#cursor-copilot-cli-aider-opencode-goose-amp-kiro-and-others)):

  ```bash
  ccenv exec client-a -- env CCENV_AGENT=cursor agent
  alias aider='CCENV_AGENT=aider aider'        # when you start it without ccenv exec
  ```
- `ccenv exec` does not write a session record (only `ccenv run` does). An agent started this way
  is identified by `$CCENV`: it may use its own profile and those in `agents_may_use` at the same
  or a lower level, and power profiles always need you. See [agents.md](agents.md).

---

## Codex through a gateway

ccenv sets `CODEX_HOME` for a `codex:` block but does not write Codex's `config.toml` in this
release. To send Codex through an LLM gateway without exporting the token, write the provider
into the managed home by hand and let Codex call ccenv for the token.

1. A profile with its own Codex home, using the gateway role:

   ```yaml
   roles:
     corp-gateway:
       claude: { provider: gateway, base_url: https://llm-gateway.corp.example, token: "sops:corp-gateway/token" }
   profiles:
     client-c-codex:
       match: ~/code/client-c
       use: [corp-gateway]
       tool: codex
       codex: { home: client-c }
   ```

2. Get the absolute helper command for that token. `ccenv desktop client-c-codex --print-3p`
   prints it as `inferenceCredentialHelper` (the program) and `inferenceCredentialHelperArgs` (its
   arguments).

3. Create `~/.local/state/ccenv/homes/codex/client-c/config.toml` (run `ccenv run client-c-codex`
   once first, or create the directory with mode 0700):

   ```toml
   model_provider = "corp"

   [model_providers.corp]
   name = "Corp gateway"
   base_url = "https://llm-gateway.corp.example/v1"
   wire_api = "responses"          # or what your gateway supports

   [model_providers.corp.auth]
   command = "/usr/bin/python3"    # inferenceCredentialHelper from step 2
   args = ["/home/ada/.local/bin/ccenv", "secret", "--ref", "sops:corp-gateway/token"]
   ```

Codex then runs the command when it needs a token; nothing secret is in its environment. The
keys follow Codex's configuration reference; this recipe is not yet verified end to end. A
project's `.codex/config.toml` cannot set providers, which is why this goes in the home.

Codex may drop variables whose names contain `KEY`, `SECRET` or `TOKEN` from the commands it runs
(its `shell_environment_policy`); sources disagree on the default, so this is not yet verified. If
it does, `GH_TOKEN` does not reach those commands, `gh` reports "not logged in" (the `github`
module fails closed), and git still works through its credential helper.

---

## Read-only by default, power on demand

"I want my personal projects read-only, with PowerUser available when I ask for it." Keep the
read-only profile as the one that matches, and define the power profile on top of it, with no
`match` and with `confirm`:

```yaml
roles:
  aws-personal-ro:    { level: readonly, aws: { profile: personal-readonly, region: eu-west-1 } }
  aws-personal-power: { level: power,    aws: { profile: personal-poweruser } }
profiles:
  personal:
    match: ~/code/personal
    use: [aws-personal-ro]
  personal-power:
    use: [personal, aws-personal-power]
    confirm: true
```

Everyday sessions in `~/code/personal` are read-only. For one elevated command, run it yourself:

```bash
ccenv exec personal-power -- terraform apply
# Elevate this shell -> personal-power (power) to run: terraform apply.
# Type the profile name to continue: personal-power
```

You type the profile name each time. Agents can never use `personal-power`: they get a
`ccenv-stop:` refusal with the command for you to run in your own terminal.

For a real boundary, give the power role a separate SSO start URL or an MFA requirement, so the
read-only login cannot mint it.

---

## Extra deny rules

A permission set is a role with only `permissions`. These add file rules for credential stores
that have no built-in module:

```yaml
roles:
  deny-tool-credentials:
    permissions:
      deny:
        - "Read(~/.docker/config.json)"
        - "Read(~/.kube/config)"
        - "Read(~/.npmrc)"
        - "Read(~/.pgpass)"
        - "Read(~/.vault-token)"
        - "Read(~/.config/glab-cli/**)"
```

Rules are unioned with the level's built-in rules and cannot be removed by a later layer. They are
Claude Code rules: they steer an agent that follows them, they do not stop other programs.

---

## Stronger isolation: devcontainer or VM

Agent rules and ccenv's checks are a seatbelt. When an agent must not be able to reach other
clients' credentials at all, run it where those credentials do not exist:

- Run the agent inside a devcontainer or VM per client. Do **not** mount `~/.config/ccenv`, the
  age key, `~/.aws`, `~/.config/gh`, `~/.azure`, `~/.config/gcloud` or `~/.claude` from the host.
- Inside, install ccenv with a manifest that holds only that client's profiles, and credentials
  scoped to that client: a fine-grained GitHub token for its repositories, a read-only cloud role,
  an age key that exists only in the container.
- For AWS, log in inside the container to a narrow permission set (`aws sso login --use-device-code`),
  or paste short-lived credentials from `ccenv aws-handoff <profile> --stdout` (they expire; the
  default is one hour for static keys, the role's session lifetime for SSO profiles).
- Run setup steps (`ccenv init`, `ccenv secret set`, `ccenv login`) in a terminal inside the
  container, not through the agent: they are human-only.

This recipe is guidance, not yet verified with a specific devcontainer or VM setup.

---

## direnv, mise and AWS credential tools

- **direnv:** `ccenv instructions --direnv` prints a `use_ccenv` function for
  `~/.config/direnv/direnvrc` (also in `contrib/direnvrc`). Add `use ccenv` (or
  `use ccenv <profile>`) to a project's `.envrc`. Only non-secret variables reach your shell.
  With `github.auth`, the token is not among them: `gh` in that shell uses its own keyring login
  (ccenv does not point it at the empty fail-closed config there), while `git push` uses
  ccenv's credential helper. Run `gh` through `ccenv exec` to act as the profile's account.
- **mise:** source the same output from `mise.toml`:

  ```toml
  [env]
  _.source = "/home/ada/.config/mise/ccenv-env.sh"   # the script: eval "$(ccenv shell --no-secrets)"
  ```

  Not yet verified with a current mise release.

- **aws-vault, granted:** for static IAM keys or auto-login, point `aws.profile` at an AWS profile
  whose `credential_process` is `aws-vault export --format=json <p>` or
  `granted credential-process --profile <p>`. ccenv sets `AWS_PROFILE`; the AWS CLI does the rest.
- **fnox** (secrets per project) can sit next to ccenv: ccenv picks the identity, fnox supplies
  application secrets. A `cmd:` ref can call fnox's command that prints one secret (check
  fnox's docs for its exact form).
