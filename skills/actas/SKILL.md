---
name: actas
description: Check which credentials (LLM provider, cloud accounts, GitHub/git identity) this session uses, and switch to another actas profile or ask the user for one when a command fails with an auth or permission error. Use before cloud, GitHub, registry or deploy commands.
allowed-tools: Bash(actas status), Bash(actas status *), Bash(actas list), Bash(actas list *)
---

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

## How to use this skill

1. Run `actas status --json`. Read these fields:
   - `session.profile` / `session.source`: if `session.profile` is null, this
     session has no actas profile (actas did not start it and no synced
     settings name one). Your environment may not hold the reported profile's
     credentials, and `actas exec` works only for this directory's readonly
     profile. Ask the user to start you with `actas run`.
   - `profile` and `level`: the profile actas resolved (the session's, or the
     directory's when there is no session), and `readonly`, `standard` or `power`.
   - `mismatch`: true when the current directory is matched to another profile
     (not when only the fallback profile covers it).
   - `agents_may_use`: profiles you may try with `actas exec` (same or lower
     level, no `confirm`; `"*"` means any profile; in a nested run the outer
     session's list applies).
   - `identities`: the AWS profile, GitHub account, git identity and LLM provider in effect.
2. Run the command when `session.profile` is set and the profile is right.
3. When you need another profile, run `actas list --json` to see the names,
   then `actas exec <profile> --reason "<why>" -- <one command>`. Claude asks
   the user before it runs `actas exec`, and actas then decides on its own.
4. When the first stderr line starts with `actas-stop:`, relay the `ask_user`
   text word for word and stop. Do not retry.
5. When a command fails with an auth or permission error and there is no
   `actas-stop:` line, run `actas status --probe`. It exits 4 with
   `actas-stop: code=expired` when a login has expired.
