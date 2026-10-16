---
name: unquoted-colon
title: Unquoted Colon
summary: Asserts an unquoted colon in a value produces an actionable error.
description: Use when: the user asks about something. Do NOT use for: anything else.
author: Fixture Author
type: community
status: Published
language: en
prompt: run it
tools:
  - Read
---

# Unquoted colon

This frontmatter is not valid YAML. The old line-splitting parser accepted it
silently and truncated `description` at the first colon. A strict parser must
reject it with a message that tells the contributor to quote the value.
