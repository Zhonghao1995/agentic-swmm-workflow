---
name: skill-author
description: Draft a well-formed new skill (a SKILL.md scaffold, optionally with scripts/references) from a described recurring need, for human review and approval. Use whenever a repeated workflow gap has no existing skill covering it, when someone wants to propose or create a new skill or capability, or when an agentic system detects a recurring problem that warrants a brand-new skill rather than editing an existing one. Domain-general — works for any modeling or workflow domain; SWMM is just one example.
---

# Skill author

Turn a description of a recurring need into a **draft skill** that a human can review and approve. This skill writes proposals; it never installs, activates, or edits skills on its own — a freshly drafted skill is a proposal, not a verified capability.

It is domain-general: nothing here is specific to stormwater or SWMM. The same recipe produces a skill for any modeling or workflow domain.

## When to use

- A recurring problem keeps showing up and **no existing skill covers it** — the gap itself is the trigger to propose a new one.
- Someone asks to "create / propose / draft a new skill" for some capability.
- An agentic system (e.g. a memory or lessons-learned layer) flags a repeated pattern that warrants a new skill rather than a tweak to an existing one.

If an existing skill already covers most of the need, **improve that one instead** — don't create a near-duplicate (see step 2).

## How to draft a skill

1. **Capture intent.** Pin down three things before writing: (a) what should this skill let the agent *do*? (b) *when* should it trigger — what phrases or contexts? (c) what does it *produce* (output format)? Pull answers from the conversation or the evidence you were handed; only ask the human for what's genuinely missing.

2. **Check for overlap first.** List the existing skills and check whether one already does most of this. If so, propose refining that skill instead of adding a new one — avoiding skill sprawl keeps the library discoverable.

3. **Fill the template.** Copy `assets/SKILL.template.md` and fill it in. The two fields that matter most:
   - `name` — kebab-case, matches the folder name.
   - `description` — this is *how the agent decides to use the skill*, so make it specific and slightly pushy: say what it does AND the concrete situations it should trigger in. A vague description means the skill never fires.

   Keep the body lean and **explain the why** behind each instruction — a capable model follows reasoning better than rigid ALL-CAPS rules. See `references/skill-format.md` for the full recipe (anatomy, progressive disclosure, writing patterns).

4. **Validate.** Run the checker and fix anything it flags:
   ```bash
   python3 scripts/validate_skill.py path/to/draft-skill
   ```
   It confirms the SKILL.md has a name and a real description, the name matches the folder, and the body isn't empty.

5. **Present for approval.** Show the human the drafted skill plus the need/evidence that motivated it, and let them accept or reject. **Nothing is installed until they say yes.** Keep their part to a single yes/no — do the drafting work for them.

## What makes a good skill

Read `references/skill-format.md` before writing the body. It condenses the skill anatomy (SKILL.md + optional `scripts/` `references/` `assets/`), progressive disclosure (keep SKILL.md lean, push detail to `references/`), how to write a `description` that actually triggers, and the writing patterns that make instructions work.

## Output contract

A draft skill folder:
```
<skill-name>/
├── SKILL.md          (required: name + description frontmatter, lean body)
├── references/       (optional: detailed docs loaded on demand)
├── assets/           (optional: templates / files used in output)
└── scripts/          (optional: deterministic helpers)
```
Place the draft in a staging / proposals area for review — do **not** drop it into a live skills directory until the human approves.

## Safety / boundaries

- **Propose, never auto-apply.** Don't install a drafted skill, activate it, or edit existing skills without explicit human approval.
- **A draft is not a verified capability.** It still needs human review and, where the domain has them, benchmark or test verification before it's trusted.
- **No surprises.** A skill's contents must match its stated intent — no hidden behavior, no malware, nothing that exfiltrates data or does something the description doesn't admit.
- **Stay domain-general.** Don't bake one domain's assumptions (specific node names, a fixed file layout) into this authoring skill itself.
