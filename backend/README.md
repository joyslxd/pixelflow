# DeerFlow Backend

DeerFlow is a LangGraph-based AI super agent with sandbox execution, persistent memory, and extensible tool integration. The backend enables AI agents to execute code, browse the web, manage files, delegate tasks to subagents, and retain context across conversations - all in isolated, per-thread environments.

---

## Architecture

```
                        ┌──────────────────────────────────────┐
                        │          Nginx (Port 2026)           │
                        │      Unified reverse proxy           │
                        └───────┬──────────────────┬───────────┘
                                │
            /api/langgraph/*    │    /api/* (other)
            rewritten to /api/* │
                                ▼
               ┌────────────────────────────────────────┐
               │        Gateway API (8001)              │
               │        FastAPI REST + agent runtime    │
               │                                        │
               │ Models, MCP, Skills, Memory, Uploads,  │
               │ Artifacts, Threads, Runs, Streaming    │
               │                                        │
               │ ┌────────────────────────────────────┐ │
               │ │ Lead Agent                         │ │
               │ │ Middleware Chain, Tools, Subagents │ │
               │ └────────────────────────────────────┘ │
               └────────────────────────────────────────┘
```

**Request Routing** (via Nginx):
- `/api/langgraph/*` → Gateway LangGraph-compatible API - agent interactions, threads, streaming
- `/api/*` (other) → Gateway API - models, MCP, skills, memory, artifacts, uploads, thread-local cleanup
- `/` (non-API) → Frontend - Next.js web interface

---

## Core Components

### Lead Agent

The single LangGraph agent (`lead_agent`) is the runtime entry point, created via `make_lead_agent(config)`. It combines:

- **Dynamic model selection** with thinking and vision support
- **Middleware chain** for cross-cutting concerns (9 middlewares)
- **Tool system** with sandbox, MCP, community, and built-in tools
- **Subagent delegation** for parallel task execution
- **System prompt** with skills injection, memory context, and working directory guidance

### Middleware Chain

Middlewares execute in strict order, each handling a specific concern:

| # | Middleware | Purpose |
|---|-----------|---------|
| 1 | **ThreadDataMiddleware** | Creates per-thread isolated directories (workspace, uploads, outputs) |
| 2 | **UploadsMiddleware** | Injects newly uploaded files into conversation context |
| 3 | **SandboxMiddleware** | Acquires sandbox environment for code execution |
| 4 | **SummarizationMiddleware** | Reduces context when approaching token limits (optional) |
| 5 | **TodoListMiddleware** | Tracks multi-step tasks in plan mode (optional) |
| 6 | **TitleMiddleware** | Auto-generates conversation titles after first exchange |
| 7 | **MemoryMiddleware** | Queues conversations for async memory extraction |
| 8 | **ViewImageMiddleware** | Injects image data for vision-capable models (conditional) |
| 9 | **ClarificationMiddleware** | Intercepts clarification requests and interrupts execution (must be last) |

### Sandbox System

Per-thread isolated execution with virtual path translation:

- **Abstract interface**: `execute_command`, `read_file`, `write_file`, `list_dir`
- **Providers**: `LocalSandboxProvider` (filesystem) and `AioSandboxProvider` (Docker, in community/). Async runtime paths use async sandbox lifecycle hooks so startup, readiness polling, and release do not block the event loop.
- **Virtual paths**: `/mnt/user-data/{workspace,uploads,outputs}` → thread-specific physical directories
- **Skills path**: `/mnt/skills` → `deer-flow/skills/` directory
- **Skills loading**: Recursively discovers nested `SKILL.md` files under `skills/{public,custom}` and preserves nested container paths
- **File-write safety**: `str_replace` serializes read-modify-write per `(sandbox.id, path)` so isolated sandboxes keep concurrency even when virtual paths match
- **Tools**: `bash`, `ls`, `read_file`, `write_file`, `str_replace` (`write_file` overwrites by default and exposes `append` for end-of-file writes; `bash` is disabled by default when using `LocalSandboxProvider`; use `AioSandboxProvider` for isolated shell access)

### Subagent System

Async task delegation with concurrent execution:

- **Built-in agents**: `general-purpose` (full toolset) and `bash` (command specialist, exposed only when shell access is available)
- **Concurrency**: Max 3 subagents per turn, 15-minute timeout
- **Execution**: Background thread pools with status tracking and SSE events
- **Flow**: Agent calls `task()` tool → executor runs subagent in background → polls for completion → returns result

### Memory System

LLM-powered persistent context retention across conversations:

- **Automatic extraction**: Analyzes conversations for user context, facts, and preferences
- **Structured storage**: User context (work, personal, top-of-mind), history, and confidence-scored facts
- **Debounced updates**: Batches updates to minimize LLM calls (configurable wait time)
- **System prompt injection**: Top facts + context injected into agent prompts
- **Storage**: JSON file with mtime-based cache invalidation

### Tool Ecosystem

| Category | Tools |
|----------|-------|
| **Sandbox** | `bash`, `ls`, `read_file`, `write_file`, `str_replace` |
| **Built-in** | `present_files`, `ask_clarification`, `view_image`, `task` (subagent) |
| **Community** | Tavily (web search), Jina AI (web fetch), Firecrawl (scraping), DuckDuckGo (image search) |
| **MCP** | Any Model Context Protocol server (stdio, SSE, HTTP transports) |
| **Skills** | Domain-specific workflows injected via system prompt |

### Gateway API

FastAPI application providing REST endpoints for frontend integration:

| Route | Purpose |
|-------|---------|
| `GET /api/models` | List available LLM models |
| `GET/PUT /api/mcp/config` | Manage MCP server configurations |
| `GET/PUT /api/skills` | List and manage skills |
| `POST /api/skills/install` | Install skill from `.skill` archive |
| `GET /api/memory` | Retrieve memory data |
| `POST /api/memory/reload` | Force memory reload |
| `GET /api/memory/config` | Memory configuration |
| `GET /api/memory/status` | Combined config + data |
| `POST /api/threads/{id}/uploads` | Upload files (auto-converts PDF/PPT/Excel/Word to Markdown, rejects directory paths, auto-renames duplicate filenames in one request) |
| `GET /api/threads/{id}/uploads/list` | List uploaded files |
| `DELETE /api/threads/{id}` | Delete DeerFlow-managed local thread data after LangGraph thread deletion; unexpected failures are logged server-side and return a generic 500 detail |
| `GET /api/threads/{id}/artifacts/{path}` | Serve generated artifacts |

### IM Channels

The IM bridge supports Feishu, Slack, and Telegram. Slack and Telegram still use the final `runs.wait()` response path, while Feishu now streams through `runs.stream(["messages-tuple", "values"])` and updates a single in-thread card in place.

For Feishu card updates, DeerFlow stores the running card's `message_id` per inbound message and patches that same card until the run finishes, preserving the existing `OK` / `DONE` reaction flow.

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- API keys for your chosen LLM provider

### Installation

```bash
cd backend
make install
```

### Configuration

PixelFlow 后端现在使用 profile YAML，类似 Spring Boot 的 `application-dev.yml` / `application-prod.yml`：

| File | Purpose |
| --- | --- |
| `config.dev.yml` | Development/test profile. Swagger is enabled, sqlite is used by default. |
| `config.prod.yml` | Production profile. Swagger is disabled by default and output paths are production-oriented. |

Edit the chosen file directly. Model keys, content-app auth settings, MySQL URL, tracing keys, and output paths are all documented with Chinese comments in the YAML itself. The loader maps these YAML values into the environment variables that existing gateway/skill code reads.

PixelFlow does **not** issue its own login token anymore. Every protected `/agent/**` request must carry `Authorization: Bearer <content-app-jwt>`. The gateway only reads the JWT subject as the username, calls content-app `/api/auth/verify` to let content-app validate the token and disabled-user state, and passes the same `Authorization` through to content-app/Borgrise generation APIs for billing.

### Running

**Backend Only** (from backend directory):

```bash
make dev                                  # Loads config.dev.yml and enables reload
PIXELFLOW_CONFIG_ENV=dev make gateway     # Loads config.dev.yml without reload
PIXELFLOW_CONFIG_ENV=prod make gateway    # Loads config.prod.yml without reload
PIXELFLOW_CONFIG_FILE=/abs/path/custom.yml make gateway
```

Direct access: Gateway at http://localhost:8001, unless `gateway.port` is changed in the selected YAML.

Command-line environment variables still have the highest priority for temporary overrides:

```bash
GATEWAY_PORT=8123 PIXELFLOW_CONFIG_ENV=dev make gateway
```

---

## Project Structure

```
backend/
├── src/
│   ├── agents/                  # Agent system
│   │   ├── lead_agent/         # Main agent (factory, prompts)
│   │   ├── middlewares/        # 9 middleware components
│   │   ├── memory/             # Memory extraction & storage
│   │   └── thread_state.py    # ThreadState schema
│   ├── gateway/                # FastAPI Gateway API
│   │   ├── app.py             # Application setup
│   │   └── routers/           # 6 route modules
│   ├── sandbox/                # Sandbox execution
│   │   ├── local/             # Local filesystem provider
│   │   ├── sandbox.py         # Abstract interface
│   │   ├── tools.py           # bash, ls, read/write/str_replace
│   │   └── middleware.py      # Sandbox lifecycle
│   ├── subagents/              # Subagent delegation
│   │   ├── builtins/          # general-purpose, bash agents
│   │   ├── executor.py        # Background execution engine
│   │   └── registry.py        # Agent registry
│   ├── tools/builtins/         # Built-in tools
│   ├── mcp/                    # MCP protocol integration
│   ├── models/                 # Model factory
│   ├── skills/                 # Skill discovery & loading
│   ├── config/                 # Configuration system
│   ├── community/              # Community tools & providers
│   ├── reflection/             # Dynamic module loading
│   └── utils/                  # Utilities
├── docs/                       # Documentation
├── tests/                      # Test suite
├── langgraph.json              # LangGraph graph registry for tooling/Studio compatibility
├── pyproject.toml              # Python dependencies
├── Makefile                    # Development commands
└── Dockerfile                  # Container build
```

`langgraph.json` is not the default service entrypoint.  The scripts and Docker
deployments run the Gateway embedded runtime; the file is kept for LangGraph
tooling, Studio, or direct LangGraph Server compatibility.

---

## Configuration

### Main Configuration (`config.dev.yml` / `config.prod.yml`)

Gateway startup first loads a PixelFlow profile file, then points DeerFlow's
`DEER_FLOW_CONFIG_PATH` at the same YAML. This means the profile file is both:

- the PixelFlow business configuration entry (`gateway`, `auth`, `pixelflow`, `borgrise`);
- the DeerFlow harness configuration entry (`models`, `sandbox`, `database`, `skills`, tracing).

Profile selection:

- `PIXELFLOW_CONFIG_ENV=dev` loads `config.dev.yml`.
- `PIXELFLOW_CONFIG_ENV=prod` loads `config.prod.yml`.
- `PIXELFLOW_CONFIG_FILE=/abs/path/custom.yml` loads an explicit file and wins over `PIXELFLOW_CONFIG_ENV`.

Key sections:
- `gateway` - FastAPI host/port, OpenAPI docs, CORS.
- `pixelflow` - MySQL URL, media provider (`media_skill`), edit/render skill (`edit_skill`), draft/render/font paths.
- `borgrise` - content-app/Borgrise Client configuration used when `media_skill` is `borgrise`: base URL, `/api/auth/verify` remote validation switch, 10-second login-check timeout, project ID, retry policy, and separate polling limits for video generation (1 hour), image generation (10 minutes), and video analysis/decompose (20 minutes). User tokens must come from the incoming `Authorization` header, not from YAML.
- `models` - LLM configurations with class paths, API keys, thinking/vision flags.
- `sandbox` - Execution environment provider.
- `database` - DeerFlow persistence/checkpointer backend.
- `skills` - Skills directory paths.
- `tracing` - LangSmith/Langfuse settings.
- `environment.variables` - Escape hatch for uncommon env vars.

Provider note:
- `models[*].use` references provider classes by module path (for example `langchain_openai:ChatOpenAI`).
- If a provider module is missing, DeerFlow now returns an actionable error with install guidance (for example `uv add langchain-google-genai`).

### Extensions Configuration (`extensions_config.json`)

MCP servers and skill states in a single file:

```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    },
    "secure-http": {
      "enabled": true,
      "type": "http",
      "url": "https://api.example.com/mcp",
      "oauth": {
        "enabled": true,
        "token_url": "https://auth.example.com/oauth/token",
        "grant_type": "client_credentials",
        "client_id": "$MCP_OAUTH_CLIENT_ID",
        "client_secret": "$MCP_OAUTH_CLIENT_SECRET"
      }
    }
  },
  "skills": {
    "pdf-processing": {"enabled": true}
  }
}
```

### Environment Variable Overrides

Profile YAML is the primary configuration source. A shell environment variable
still wins for temporary one-off overrides, for example:

```bash
GATEWAY_PORT=8123 PIXELFLOW_CONFIG_ENV=dev make gateway
```

Common selectors:

- `PIXELFLOW_CONFIG_ENV` - Selects `config.dev.yml` or `config.prod.yml`.
- `PIXELFLOW_CONFIG_FILE` - Uses an explicit YAML file path.
- `DEER_FLOW_EXTENSIONS_CONFIG_PATH` - Overrides `extensions_config.json` / `mcp_config.json`.

### LangSmith Tracing

DeerFlow has built-in [LangSmith](https://smith.langchain.com) integration for observability. When enabled, all LLM calls, agent runs, tool executions, and middleware processing are traced and visible in the LangSmith dashboard.

**Setup:**

1. Sign up at [smith.langchain.com](https://smith.langchain.com) and create a project.
2. Edit the selected profile YAML:

```yaml
tracing:
  langsmith:
    enabled: true
    endpoint: "https://api.smith.langchain.com"
    api_key: "lsv2_pt_xxxxxxxxxxxxxxxx"
    project: "pixelflow-prod"
```

**Legacy variables:** The `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, and `LANGCHAIN_ENDPOINT` variables are also supported for backward compatibility. `LANGSMITH_*` variables take precedence when both are set.

### Langfuse Tracing

DeerFlow also supports [Langfuse](https://langfuse.com) observability for LangChain-compatible runs.

Edit the selected profile YAML:

```yaml
tracing:
  langfuse:
    enabled: true
    public_key: "pk-lf-xxxxxxxxxxxxxxxx"
    secret_key: "sk-lf-xxxxxxxxxxxxxxxx"
    base_url: "https://cloud.langfuse.com"
```

If you are using a self-hosted Langfuse deployment, set `LANGFUSE_BASE_URL` to your Langfuse host.

### Dual Provider Behavior

If both LangSmith and Langfuse are enabled, DeerFlow initializes and attaches both callbacks so the same run data is reported to both systems.

If a provider is explicitly enabled but required credentials are missing, or the provider callback cannot be initialized, DeerFlow raises an error when tracing is initialized during model creation instead of silently disabling tracing.

**Docker:** Prefer setting `PIXELFLOW_CONFIG_ENV=prod` or `PIXELFLOW_CONFIG_FILE=/app/backend/config.prod.yml` for containerized deployments. Tracing should be configured in the selected YAML profile.

---

## Development

### Commands

```bash
make install    # Install dependencies
make dev        # Run Gateway API + embedded agent runtime with config.dev.yml and reload
make gateway    # Run Gateway API without reload; uses PIXELFLOW_CONFIG_ENV, default dev
make gateway-prod  # Run Gateway API without reload with config.prod.yml
make lint       # Run linter (ruff)
make format     # Format code (ruff)
make detect-blocking-io  # Inventory blocking IO that may block the backend event loop
```

### Code Style

- **Linter/Formatter**: `ruff`
- **Line length**: 240 characters
- **Python**: 3.12+ with type hints
- **Quotes**: Double quotes
- **Indentation**: 4 spaces

### Testing

```bash
uv run pytest
```

`make detect-blocking-io` statically scans backend business code for blocking
IO that may run on the backend event loop and is not test-coverage-bound. It
prints a concise summary for human review and writes complete JSON findings to
`.deer-flow/blocking-io-findings.json` at the repository root (regardless of
whether the target is invoked from the repo root or from `backend/`). JSON
findings include both broad IO category and review-oriented fields such as
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code`. `priority` is a deterministic review ordering from the operation type,
not proof of a bug. Bare-name same-file calls are resolved by function name,
so duplicate helper names in one file can conservatively over-report async
reachability.

---

## Technology Stack

- **LangGraph** (1.0.6+) - Agent framework and multi-agent orchestration
- **LangChain** (1.2.3+) - LLM abstractions and tool system
- **FastAPI** (0.115.0+) - Gateway REST API
- **langchain-mcp-adapters** - Model Context Protocol support
- **agent-sandbox** - Sandboxed code execution
- **markitdown** - Multi-format document conversion
- **tavily-python** / **firecrawl-py** - Web search and scraping

---

## Documentation

- [Configuration Guide](docs/CONFIGURATION.md)
- [Architecture Details](docs/ARCHITECTURE.md)
- [API Reference](docs/API.md)
- [File Upload](docs/FILE_UPLOAD.md)
- [Path Examples](docs/PATH_EXAMPLES.md)
- [Context Summarization](docs/summarization.md)
- [Plan Mode](docs/plan_mode_usage.md)
- [Setup Guide](docs/SETUP.md)

---

## License

See the [LICENSE](../LICENSE) file in the project root.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.
