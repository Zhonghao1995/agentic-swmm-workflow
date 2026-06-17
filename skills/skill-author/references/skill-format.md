# What makes a good skill

A condensed authoring recipe (distilled from the skill-creator approach). Read this before writing a new skill's body.

## Anatomy

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter — name, description (both required)
│   └── Markdown body — the instructions
└── optional bundled resources:
    ├── scripts/    — executable helpers for deterministic / repetitive work
    ├── references/ — docs loaded into context only when needed
    └── assets/     — files used in the output (templates, icons, fonts)
```

## Progressive disclosure (the core idea)

A skill loads in three levels — keep the cheap things small so the expensive ones load only when needed:

1. **name + description** — always in context (~100 words). The triggering surface.
2. **SKILL.md body** — loaded when the skill triggers. Keep it lean (well under ~500 lines).
3. **Bundled resources** — loaded on demand; scripts can run without being read into context.

If the body grows past ~500 lines, move detail into `references/*.md` and point to it with a one-line "read X when you need Y."

## The description field (most important)

The description is the *only* thing the agent sees when deciding whether to use the skill. So:

- Say **what it does** AND **when to trigger** — concrete phrases, contexts, file types.
- Be a little **pushy** — models tend to *under*-trigger skills. e.g. "Use this whenever the user mentions X, Y, or Z, even if they don't say the word 'skill'."
- One or two sentences. Put everything else in the body.

## Writing the body

- **Imperative voice.** "Read the manifest", not "the skill reads the manifest."
- **Explain the why.** A capable model follows reasoning better than rigid rules. Prefer "do X because Y" over "ALWAYS DO X." If you catch yourself writing ALL-CAPS MUSTs, reframe and explain instead.
- **Output formats** — when the output shape matters, give an explicit template.
- **Examples** — a couple of Input → Output examples beat a paragraph of prose.
- **General, not overfit.** Write for the whole class of cases, not the single example in front of you.

## Bundle a script when work repeats

If every use of the skill would re-derive the same helper (parse the same file, build the same artifact), write it once into `scripts/` and have the skill call it. Deterministic, faster, consistent.

## Lack of surprise (safety)

A skill's contents must match its stated intent. No malware, no exploit code, nothing that does something the description doesn't admit. If a described skill would surprise the user in what it actually does, don't write it that way.
