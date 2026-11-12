---
name: aws-handoff
description: Get short-lived AWS credentials into a session that cannot see the local AWS SSO cache (for example a Claude Code cloud session). The user runs `actas aws-handoff` in their own terminal and pastes the result; the skill then verifies the identity.
argument-hint: "[profile] [--profile NAME] [--duration SECONDS] [--stdout] [--login]"
disable-model-invocation: true
allowed-tools: Bash(aws sts get-caller-identity), Bash(aws sts get-caller-identity *)
---

# AWS credential handoff

Cloud sessions cannot see the user's `~/.aws` or SSO cache, so they have no
AWS access until a human hands credentials over. `actas aws-handoff` mints
short-lived ones on the user's machine. It is **human-only**: inside any agent
session, actas refuses it with an `actas-stop: ... reason=human_only` line.
So never run it yourself, and never ask for long-lived keys.

## Steps

1. Tell the user, word for word (fill in the arguments, if any):

   > On your own machine, in a terminal (not inside an agent session), run
   > `actas aws-handoff $ARGUMENTS` and paste the result here.

   By default the block goes to their clipboard. With `--stdout` it is printed
   instead.

2. If `actas aws-handoff` was already run inside this session and printed a
   line starting with `actas-stop:`, it was refused. Show the user the
   `ask_user` text exactly, which tells them to run it in their own terminal,
   and stop. Do not retry.

3. If the user reports that the AWS SSO session has expired (the command exits
   with `actas-stop: code=expired`), tell them to run
   `actas login <profile>` in their terminal first, then retry step 1. Running
   `actas aws-handoff <profile> --login` does both. Never run `aws sso login`
   yourself.

4. The pasted block is a short shell snippet that replaces `~/.aws/credentials`
   (and `~/.aws/config` when a region is known) with a `[default]` profile in
   this session. After it has run, run
   `aws sts get-caller-identity` and report the account and the role (from the
   ARN) to the user.

## Important

- The credentials become part of this session's transcript. Keep the TTL
  short (`--duration` applies when actas has to trade static keys for a
  session token), and prefer a profile with a narrow role.
- actas never hands over a long-lived access key. It trades static IAM keys
  for a session token first.
- Do not repeat the credential values in your replies.
