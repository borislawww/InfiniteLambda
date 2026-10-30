# Prompts given to the coding agent

In order:

1. `initial-prompt.md`: the master prompt, verbatim.
2. The full Hayden skill text, with the note: "this is the skill and the agent orchestration that I want to have in my folder and for the master prompt which I will provide to use". It is stored verbatim at `.claude/commands/hayden.md` and not duplicated here.
3. "attaching the PDF's". The attachments did not reach the cloud sandbox.
4. "I want to start clean remove everything that you have created in the repo except from the agents orchestration". The repo was reset to only `.claude/commands/hayden.md`.
5. "These are the two PDF from which you can get info from the promp that I have provided. Now please continue with the master prompt that was provided earlier." The user then named the two references as the docs.informatica.com URLs for the Designer Guide and the Transformation Guide.

Environment facts, recorded honestly:
- The Hayden website (hayden.boyanbalevengineering.com) and docs.informatica.com cannot be reached from the cloud sandbox. The egress proxy answers 403 to CONNECT.
- The skill was therefore installed from the text the user pasted.
- Informatica semantics were looked up through a separate web-fetch agent. See `docs/00-recon.md` §8 for what was verified and what was not.

The subagent briefs written by Hayden (Hashi, Ahsoka, and any defect loops) are in `subagent-briefs.md`.
