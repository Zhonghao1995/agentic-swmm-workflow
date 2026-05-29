# LLM Providers

The `aiswmm` interactive runtime drives its planner with a large language
model. Two API-key backends are supported, both using standard
function-calling. The deterministic `rule` planner needs no provider at all;
everything below applies only to the LLM planner.

| Provider    | Backend                  | Authentication                       | Install          |
| ----------- | ------------------------ | ------------------------------------ | ---------------- |
| `openai`    | OpenAI Responses API     | `OPENAI_API_KEY` (per-token billing) | ships by default |
| `anthropic` | Anthropic Messages API   | `ANTHROPIC_API_KEY` (per-token billing) | ships by default |

Both providers are pure-stdlib `urllib` clients — there is no SDK to install
and no subprocess to spawn. `openai` is the **default** provider. The fastest
way to get going is:

```bash
aiswmm login        # stores your OpenAI API key (the default provider)
```

`anthropic` is an explicit opt-in (also billed per token). To use it, run
`aiswmm login --anthropic` or pass `--provider anthropic` per invocation.

## Switching providers

The active provider is the `provider.default` config key. Set it once:

```bash
aiswmm config set provider.default openai      # or: anthropic
```

Per-invocation override (does not persist):

```bash
aiswmm --provider openai     "summarise this model"
aiswmm --provider anthropic  "summarise this model"
```

Each provider keeps its own model snapshot under a provider-named config
section:

```bash
aiswmm config set openai.model gpt-5.5
aiswmm config set anthropic.model claude-sonnet-4-6
```

Both providers require a model. `aiswmm` ships sensible per-provider defaults
(`openai.model = gpt-5.5`, `anthropic.model = claude-sonnet-4-6`), so you only
need to set these to pin a different snapshot.

`aiswmm model --provider <name> --model <snapshot>` is an equivalent
shorthand for the two steps above.

## Authentication per provider

Keys are stored in `~/.aiswmm/env` (file mode 0600) by `aiswmm login` and are
never echoed back. They can also be exported directly into your shell. Key
resolution checks, in order: the environment variable, `~/.aiswmm/env`, then
the `[<provider>]` section of `~/.aiswmm/config.toml`.

### `openai` (default)

Store an API key with `aiswmm login --openai` (writes `OPENAI_API_KEY` to
`~/.aiswmm/env`, sets `provider.default = openai` and `openai.model = gpt-5.5`),
or export it directly:

```bash
aiswmm login --openai      # prompts for the key, never echoes it
# or:
export OPENAI_API_KEY="sk-..."
```

### `anthropic` (opt-in)

Store an API key with `aiswmm login --anthropic` (writes `ANTHROPIC_API_KEY`
to `~/.aiswmm/env`, sets `provider.default = anthropic` and
`anthropic.model = claude-sonnet-4-6`), or export it directly:

```bash
aiswmm login --anthropic   # prompts for the key, never echoes it
# or:
export ANTHROPIC_API_KEY="sk-ant-..."
```

The provider hits `https://api.anthropic.com/v1/messages` directly with the
`anthropic-version: 2023-06-01` header. It advertises aiswmm's registered
tools verbatim, so the model can only call those tools — standard,
predictable function-calling.

`aiswmm doctor` (and `aiswmm login --status`) report the default provider and
whether each provider's API key is present. See
[api-key-configuration.md](api-key-configuration.md) for the persisted
`~/.aiswmm/env` flow.

## Rate limits and billing

Both providers are billed per token against your own account. `aiswmm` does
not pre-fetch quota metadata, does not retry on a rate-limit response, and
does not parallelise requests — each is subject to the rate and spend limits
your OpenAI / Anthropic account enforces. A provider is never selected
silently: switching the default or passing `--provider` is always explicit.
