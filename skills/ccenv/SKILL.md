---
name: ccenv
description: Check which credentials (LLM provider, cloud accounts, GitHub/git identity) this session uses, and switch to another ccenv profile or ask the user for one when a command fails with an auth or permission error. Use before cloud, GitHub, registry or deploy commands.
allowed-tools: Bash(ccenv status), Bash(ccenv status *), Bash(ccenv list), Bash(ccenv list *)
---

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

## How to use this skill

1. Run `ccenv status --json`. Read these fields:
   - `session.profile` / `session.source`: if `session.profile` is null, this
     session has no ccenv profile (ccenv did not start it and no synced
     settings name one). Your environment may not hold the reported profile's
     credentials, and `ccenv exec` works only for this directory's readonly
     profile. Ask the user to start you with `ccenv run`.
   - `profile` and `level`: the profile ccenv resolved (the session's, or the
     directory's when there is no session), and `readonly`, `standard` or `power`.
   - `mismatch`: true when the current directory is matched to another profile
     (not when only the fallback profile covers it).
   - `agents_may_use`: profiles you may try with `ccenv exec` (same or lower
     level, no `confirm`; in a nested run the outer session's list applies).
   - `identities`: the AWS profile, GitHub account, git identity and LLM provider in effect.
2. Run the command when `session.profile` is set and the profile is right.
3. When you need another profile, run `ccenv list --json` to see the names,
   then `ccenv exec <profile> --reason "<why>" -- <one command>`. Claude asks
   the user before it runs `ccenv exec`, and ccenv then decides on its own.
4. When the first stderr line starts with `ccenv-stop:`, relay the `ask_user`
   text word for word and stop. Do not retry.
5. When a command fails with an auth or permission error and there is no
   `ccenv-stop:` line, run `ccenv status --probe`. It exits 4 with
   `ccenv-stop: code=expired` when a login has expired.
