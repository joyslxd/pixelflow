# PixelFlow

An AI Agent platform for e-commerce short-video generation, built on a modular
LangGraph harness. It turns product information into shoppable short videos
through a staged pipeline, and (P1) supplements this with AI-powered PPT and
image generation.

## Architecture

PixelFlow's backend is a lean extraction of the [DeerFlow](https://github.com/bytedance/deer-flow)
harness: the FastAPI gateway, LangGraph runtime/checkpointer, sandbox, MCP
client, model/config, and persistence layers are kept; the IM chat-channel
integrations are removed. On top of that infrastructure sits the PixelFlow
pipeline graph. See [`NOTICE`](NOTICE) for attribution.

### Pipeline (`backend/packages/harness/deerflow/pixelflow/`)

The agent is a staged state machine, not a free-form ReAct loop. A task moves
through five phases (PRD):

```
采集 intake → 策划 creative → [Brief 人工确认] → 生成 generate → 剪辑 edit → 质检 qc
```

- **Brief review** is a human-in-the-loop `interrupt()` gate: approve → generate,
  revise → back to creative.
- **QC** loops back to generate on failure, bounded by `MAX_QC_ATTEMPTS`.
- `TaskState` (`state.py`) is the single state object threaded through every node.

The graph is registered in [`backend/langgraph.json`](backend/langgraph.json) as
`pixelflow`.

## Develop

```bash
cd backend
uv sync                 # install dependencies (Python 3.12)
uv run langgraph dev    # run the graph locally
```

## Status

MVP skeleton: phase nodes are stubs with the control flow fully wired. Skill
integrations (product info extraction, Brief generation, Borgrise generation,
editing, QC) are marked as TODOs in the node bodies.
