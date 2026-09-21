---
name: find-data
description: Search for reference data matching a described need, and report each candidate's declared rights alongside it. When nothing matches, name the official source and offer to log the need on the wanted board.
argument-hint: <what you need, named as a thing, e.g. "Ohio dental board licensee roster">
---

Search the Graunt catalog for data matching `$ARGUMENTS`, using the `graunt`
MCP server's `search` tool. Search the way a librarian would: name the
**thing**, not the question. Then `fetch` the most relevant candidates.

Also check `${CLAUDE_PLUGIN_ROOT}/board/featured.json` for a hand-picked
packet on this topic; if one matches, include it and say it was featured for
fulfilling a wanted request.

Report each candidate with its **rights alongside it**, not as an afterthought —
license family, redistribution, training use, citation requirement — because a
packet you may not use for the user's purpose is not a candidate. Include the
public URL so the user can inspect or buy it themselves. This plugin never
purchases anything.

Keep it to a short list. Three well-characterised options beat ten titles.

## When nothing matches

An empty result is a normal outcome, not something to paper over with a loose
match. Do three things:

1. Say so plainly.
2. Name the official source you would go to instead, and go there if the task
   allows.
3. Offer to log the need on Graunt's wanted board so a seller can fulfil it.
   Sellers watch that label, and fulfilled requests are featured back in this
   command. If the user says yes, give them this link with the fields filled
   in from what they told you (URL-encode the values):

   `https://github.com/Grauntketplace/graunt-plugin/issues/new?labels=wanted-packet&title=Wanted%3A%20<short%20title>&body=<what%20data%20%2F%20task%20it%20serves%20%2F%20coverage%20and%20freshness%20%2F%20rights%20needed%20%2F%20would%20you%20pay>`

   Do not post it yourself; the user opens the link. It is their name on it.
