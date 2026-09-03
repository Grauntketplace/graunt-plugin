---
name: check-rights
description: Summarise what a packet's declared rights permit, for a specific intended use.
---

Given a packet id or URL, `fetch` it from the `graunt` MCP server and summarise
its declared rights **against the use the user actually has in mind**. Ask what
that use is if they have not said — "can I use this" has different answers for
reading, redistributing and training.

Cover: license family, redistribution, training use, citation requirement, and
personal data.

Answer in the form *"for what you described, this permits X and does not permit
Y"*. A list of fields is not an answer.

Two honesty rules:

- A missing or ambiguous declaration is treated as restrictive. Say which field
  was unclear rather than picking the convenient reading.
- These are the publisher's declarations, not an audit or legal advice. Say so
  once, plainly, without burying the answer in caveats.
