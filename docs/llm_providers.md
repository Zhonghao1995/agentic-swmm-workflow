# LLM Providers

The `aiswmm` interactive runtime drives its planner with a large language
model. Two backends are supported. The deterministic `rule` planner needs
no provider at all; everything below applies only to the LLM planner.

| Provider     | Backend                         | Authentication                                  | Install                 |
| ------------ | ------------------------------- | ----------------------------------------------- | ----------------------- |
| `claude_sdk` | Claude Agent SDK + `claude` CLI | Claude Pro/Max subscription OAuth, or `ANTHROPIC_API_KEY` | ships by default (core dep) |
| `openai`     | OpenAI Responses API            | `OPENAI_API_KEY` (per-token billing)             | ships by default        |

`claude_sdk` is the **default** provider: it routes the planner through your
Claude Pro/Max subscription via the local `claude` CLI at zero marginal
per-token cost. The fastest way to get going is:

```bash
aiswmm login        # authenticates the Claude subscription (claude login)
```

`openai` is an explicit opt-in (billed per token). If you prefer it, run
`aiswmm login --openai` or pass `--provider openai` per invocation.

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
aiswmm config set openai.model gpt-5.5
aiswmm config set claude_sdk.model claude-sonnet-4-5-20250929
```

`claude_sdk` may be left with **no** model set — the SDK then follows the
`claude` CLI / subscription default. Only `openai` requires an explicit model
(the shipped default is `gpt-5.5`).

`aiswmm model --provider <name> --model <snapshot>` is an equivalent
shorthand for the two steps above.

## Authentication per provider

### `claude_sdk` (default)

The Claude Agent SDK provider routes through the locally installed
`claude` command-line tool. The SDK ships as a core dependency, so the
only step is authenticating the CLI:

```bash
aiswmm login            # runs `claude login` when not already logged in
```

`aiswmm login` is idempotent: it detects an existing session and only
shells out to `claude login` when needed, then pins
`provider.default = claude_sdk`. The CLI stores OAuth credentials that the
SDK reads at call time — `aiswmm` never parses or stores those credentials
itself.

On **macOS** the `claude` CLI keeps credentials in the login **Keychain**
(not a JSON file), so detection probes
`security find-generic-password -s "Claude Code-credentials"` and inspects
the exit code only — it never requests or logs the secret. When no OAuth
session is present the SDK falls back to an `ANTHROPIC_API_KEY` environment
variable if one is set. `aiswmm doctor` (and `aiswmm login --status`) report
whether a Claude subscription is detected, whether the `claude` CLI is on
PATH, and whether `claude_agent_sdk` is importable.

The provider spawns the `claude` CLI as a child process. If the CLI is not
installed, the runtime raises a clear error pointing at the Claude Code
installation docs rather than failing mid-turn.

### `openai` (opt-in)

Store an API key with `aiswmm login --openai` (written to `~/.aiswmm/env`
at mode 0600, sets `provider.default = openai` and `openai.model = gpt-5.5`),
or export it directly:

```bash
aiswmm login --openai      # prompts for the key, never echoes it
# or:
export OPENAI_API_KEY="sk-..."
```

See [api-key-configuration.md](api-key-configuration.md) for the persisted
`~/.aiswmm/env` flow. Calls are billed per token against your OpenAI
account, so OpenAI is never selected silently when a Claude subscription is
detected — it is reachable only via explicit opt-in.

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
