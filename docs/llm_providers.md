# LLM Provider Routes

The `aiswmm` interactive runtime drives its planner with a large language
model. Connections are organized as named **routes**: a route bundles a wire
format, an endpoint, an auth convention, and a default model. Ten routes ship
out of the box, all implemented with pure-stdlib `urllib` clients (no SDK, no
subprocess). The deterministic `rule` planner needs no provider at all;
everything below applies only to the LLM planner.

| Route        | Endpoint style               | Auth                      | Notes |
| ------------ | ---------------------------- | ------------------------- | ----- |
| `openai`     | OpenAI Responses API         | `OPENAI_API_KEY`          | shipped default |
| `anthropic`  | Anthropic Messages API       | `ANTHROPIC_API_KEY`       | native tool calling |
| `codex`      | OpenAI-compatible chat       | optional gateway key      | local gateway in front of a ChatGPT subscription |
| `openrouter` | OpenAI-compatible chat       | `OPENROUTER_API_KEY`      | one key, hundreds of models |
| `deepseek`   | OpenAI-compatible chat       | `DEEPSEEK_API_KEY`        | |
| `groq`       | OpenAI-compatible chat       | `GROQ_API_KEY`            | |
| `gemini`     | OpenAI-compatible chat       | `GEMINI_API_KEY`          | Google's compatibility endpoint |
| `ollama`     | OpenAI-compatible chat       | keyless                   | local models, free |
| `lmstudio`   | OpenAI-compatible chat       | keyless                   | local models, free |
| `custom`     | OpenAI-compatible chat       | optional key              | any `/v1` endpoint: vLLM, gateways, proxies |

## Quick start

The interactive wizard is the fastest path. It detects what you already have
(exported keys, a running Ollama or LM Studio, a local gateway), then walks
route, model, key, and an optional local fallback, and verifies the
connection at the end:

```bash
aiswmm setup
```

Non-interactive setups keep working exactly as before and never prompt:

```bash
aiswmm setup --provider openai            # classic path, no wizard
aiswmm setup --provider groq --model llama-3.3-70b-versatile --fallback ollama
```

One route can also be configured directly:

```bash
aiswmm login              # key for the current default route
aiswmm login openrouter   # key for a specific route, makes it the default
aiswmm login ollama       # keyless local route, just selects it
aiswmm login --status     # default, fallback, and per-route credential state
```

## Wire formats

Three wire formats cover all ten routes:

* **OpenAI Responses API**: the `openai` route.
* **OpenAI chat/completions with function calling**: the lingua franca of
  compatible endpoints, used by `codex`, `openrouter`, `deepseek`, `groq`,
  `gemini`, `ollama`, `lmstudio`, and `custom`.
* **Anthropic Messages API**: the `anthropic` route, native tool calling.

Adding another provider that speaks one of these formats is a single entry in
the route table (`agentic_swmm/providers/routes.py`).

## Configuration

The active route is `provider.default`; every route keeps its own config
section. All of this is written by the wizard or `aiswmm login`, and can be
set by hand:

```bash
aiswmm config set provider.default openrouter
aiswmm config set provider.fallback ollama          # optional, see below
aiswmm config set openrouter.model "anthropic/claude-sonnet-4-6"
aiswmm config set custom.base_url "http://vllm.host:8000/v1"
```

Per-invocation override (does not persist):

```bash
aiswmm --provider anthropic "summarise this model"
```

Environment overrides exist for every route and beat the config file:
`AISWMM_<ROUTE>_MODEL` and `AISWMM_<ROUTE>_BASE_URL` (for example
`AISWMM_OPENAI_BASE_URL=http://localhost:8317/v1` repoints the `openai` wire
at any OpenAI-compatible server). API keys resolve through three tiers, in
order: the route's environment variable, `~/.aiswmm/env` (written by
`aiswmm login`, file mode 0600, never echoed), then the `[<route>]` section
of `~/.aiswmm/config.toml`.

## Local models

`ollama` (port 11434) and `lmstudio` (port 1234) run models on your machine,
keyless and free. The wizard detects a running server and lists the models it
actually serves, so you pick from live inventory instead of guessing ids.

## ChatGPT subscription via a local gateway (`codex`)

The `codex` route targets a local OpenAI-compatible gateway that fronts a
ChatGPT subscription. aiswmm itself stays a clean HTTP client: the gateway
owns the vendor login and quota handling, and aiswmm talks to
`http://localhost:8317/v1` (or any base URL you configure). Two widely used
open-source gateways:

The shortest path is the managed install, which picks the right build for the
machine (including Windows on ARM, where an emulated x64 Python would
otherwise report AMD64) and verifies it against the release checksums:

```bash
aiswmm gateway install
aiswmm gateway login    # opens a browser to sign in to ChatGPT
aiswmm gateway start    # serves on 127.0.0.1:8317
```

`aiswmm setup` offers the same install when you pick `codex` and nothing is
listening, so the usual path is to answer one prompt. The binary lands in
`~/.aiswmm/gateway/` and is a pinned CLIProxyAPI release (MIT).

Bring your own gateway instead, if you prefer:

```bash
# CLIProxyAPI via Homebrew, macOS. Note the formula links `cliproxyapi`,
# while the GitHub release ships `cli-proxy-api`.
brew install cliproxyapi && brew services start cliproxyapi
cliproxyapi -codex-login

# OmniRoute (default port 20128), needs Node 22+
npm install -g omniroute && omniroute
```

Then run `aiswmm setup`, pick `codex`, and the wizard finds whichever gateway
is listening. Subscription terms are between you and your model vendor;
aiswmm only speaks the open OpenAI-compatible protocol to an endpoint you
choose.

## Fallback chain

`provider.fallback` names a route that takes over when the primary fails
structurally: missing credentials, connection failures after retries, or
HTTP 401/403/429/5xx. Request bugs (other 4xx) always surface instead of
being papered over. On a mid-session switch the full conversation is
replayed into the fallback, and a single loud warning explains what happened.
A local `ollama` fallback keeps you working through outages and quota
windows:

```bash
aiswmm config set provider.fallback ollama
```

## Diagnostics

`aiswmm doctor` reports the default route, its credential state, and the
fallback route. `aiswmm login --status` prints the per-route table. Neither
ever prints a secret.

## Rate limits and billing

Keyed remote routes are billed per token against your own account. Transient
failures (HTTP 429 and 5xx) are retried with exponential backoff and honor
`Retry-After`; non-transient errors surface immediately with the exact
provider message plus the command that fixes the credential. A provider is
never selected silently: the default, the fallback, and any `--provider`
override are always explicit.
