# LLM Providers

The `aiswmm` interactive runtime drives its planner with a large language
model. Two backends are supported. The deterministic `rule` planner needs
no provider at all; everything below applies only to the LLM planner.

| Provider     | Backend                         | Authentication                                  | Optional install        |
| ------------ | ------------------------------- | ----------------------------------------------- | ----------------------- |
| `openai`     | OpenAI Responses API            | `OPENAI_API_KEY` (per-token billing)             | none — ships by default |
| `claude_sdk` | Claude Agent SDK + `claude` CLI | Claude Pro/Max subscription OAuth, or `ANTHROPIC_API_KEY` | `pip install aiswmm[claude]` |

The `openai` provider is the default. If you already export `OPENAI_API_KEY`
nothing here changes for you — skip to the bottom only if you want the
Claude path.

## Switching providers

The active provider is the `provider.default` config key. Set it once:

```bash
aiswmm config set provider.default claude_sdk     # or: openai
```

Per-invocation override (does not persist):

```bash
aiswmm --provider claude_sdk "summarise this model"
aiswmm --provider openai     "summarise this model"
```

Each provider keeps its own model snapshot under a provider-named config
section:

```bash
aiswmm config set openai.model gpt-5.5-2026-04-23
aiswmm config set claude_sdk.model claude-sonnet-4-5-20250929
```

`aiswmm model --provider <name> --model <snapshot>` is an equivalent
shorthand for the two steps above.

## Authentication per provider

### `openai`

Export an API key, or persist it with `aiswmm setup --provider openai`:

```bash
export OPENAI_API_KEY="sk-..."
```

See [api-key-configuration.md](api-key-configuration.md) for the persisted
`~/.aiswmm/env` flow. Calls are billed per token against your OpenAI
account.

### `claude_sdk`

The Claude Agent SDK provider routes through the locally installed
`claude` command-line tool. Install the optional extra and authenticate
the CLI once:

```bash
pip install aiswmm[claude]
claude login
aiswmm config set provider.default claude_sdk
```

`claude login` stores OAuth credentials that the SDK reads at call time —
`aiswmm` never parses or stores those credentials itself. When no OAuth
session is present the SDK falls back to an `ANTHROPIC_API_KEY` environment
variable if one is set. `aiswmm doctor` reports whether a Claude Code OAuth
session is present and surfaces the `ANTHROPIC_API_KEY` knob.

The provider spawns the `claude` CLI as a child process. If the CLI is not
installed, the runtime raises a clear error pointing at the Claude Code
installation docs rather than failing mid-turn.

## Rate limits

The Claude Pro/Max subscription path is **subject to subscription tier
rate limits** — see the Anthropic Claude documentation for the limits that
apply to your tier. `aiswmm` does not pre-fetch tier metadata, does not
retry on a rate-limit event, and does not parallelise requests. When a turn
is rate-limited it surfaces the limit visibly; switch to `--provider openai`
for that turn if you need to continue immediately.

The `openai` provider is subject to your OpenAI account's own rate and spend
limits.

## Terms of service — single user, single subscription

The Claude Agent SDK provider consumes your personal Claude Pro/Max
subscription quota through the `claude` CLI's OAuth credentials. This is a
**single-user, single-subscription** path. Sharing one subscription's quota
across multiple developers, shared machines, or automated CI agents may
violate the Claude subscription terms. Consult Anthropic's terms of service
before using this provider outside a single-user setup. The provider does
not attempt to circumvent rate limits and embeds no credentials on disk.
