---
name: aws-handoff
description: Mint short-lived AWS credentials for the current ccenv environment and copy a ready-to-paste block to the clipboard, for handing AWS access to a Claude Code cloud session that has no access to the local SSO cache.
argument-hint: "[environment] [--profile NAME] [--duration SECONDS]"
disable-model-invocation: true
allowed-tools: Bash(ccenv:*), Bash(aws sts get-caller-identity:*)
---

# AWS credential handoff

Cloud sessions run on Anthropic infrastructure and cannot see `~/.aws`, so a
cloud session has no AWS access until credentials are handed to it explicitly.
This skill mints short-lived ones and puts them on the clipboard.

## Steps

1. Run the handoff for the requested environment (with no argument it resolves
   from the working directory):

   ```
   ccenv aws-handoff $ARGUMENTS
   ```

2. Report back to the user:
   - which AWS profile and account the credentials are for
   - when they expire
   - that the block is on the clipboard, ready to paste into the cloud session

3. If the SSO session is expired, `ccenv aws-handoff` opens a browser to
   refresh it. If that fails, tell the user to run `ccenv login <env>` and
   retry. Do not retry in a loop yourself.

## Important

Whatever is pasted into a cloud session becomes part of that session's
transcript. `ccenv` will never hand over a long-lived access key — it trades
static IAM keys for a session token first — but still keep the TTL as short as
the task allows, and prefer a profile with a narrow role.

Never print the credential block into this conversation. It goes to the
clipboard only. If the user explicitly asks to see it, remind them it will be
recorded in this transcript before doing so.
