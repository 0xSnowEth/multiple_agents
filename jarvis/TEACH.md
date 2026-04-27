# The Jarvis Architecture: Hub & Spoke

This document strips away the fluff and explains exactly how this architecture works, where everything lives, and how to scale it.

---

## 1. The Core Philosophy (Hub & Spoke)
Think of this architecture as a **Manager (Hub)** and **Specialist Workers (Spokes)**. 

- **The Hub (`core/hub/orchestrator.py`)**: The brain. It reads inbound messages, looks at the current `TaskState`, and decides *what needs to happen next*. It **never** does the heavy lifting itself. It only routes traffic and manages state.
- **The Spokes (`agents/*.py`)**: The specialized workers (Caption Agent, Strategy Agent, etc.). They have exactly one job. They take a strict input, run an LLM call, and return a strict output. They do not decide what happens next; they just do their job and return the result to the Hub.

### Why do it this way?
If you put all logic in one massive LLM prompt, it gets confused, slow, and expensive. By separating them, you can tune the Caption Agent's prompt heavily without breaking the Strategy Agent.

---

## 2. Pydantic & Langfuse Integration

### Pydantic (Strict Data Engineering)
LLMs naturally return raw text (strings). This is dangerous for production apps. 
We use **Pydantic** (`BaseModel`) to force the LLM to return strict JSON structures. 
Every Spoke defines exactly what it expects via an `Input` and `Output` Pydantic class. If the LLM hallucinates and misses a required field, Pydantic catches it immediately, triggering an automatic retry via `core/retry.py`.

### Langfuse (Observability)
When you have multiple agents talking to LLMs, debugging "why did it say this?" becomes impossible.
**Langfuse** wraps the central LLM router. Every time *any* Spoke talks to an LLM, Langfuse records:
1. The exact prompt sent.
2. The exact JSON returned.
3. Token usage and latency.
This allows you to trace a single WhatsApp message's journey through multiple agents on the Langfuse dashboard.

---

## 3. Directory & File Map

Here is where everything lives and exactly what it is responsible for:

### The Entry Points
- **`main.py`**: The start switch. It loads environment variables, starts the FastAPI server (`uvicorn`), and listens to the Meta webhook.
- **`interfaces/whatsapp.py`**: The outer shell. It receives the raw HTTP POST payload from Meta, extracts the text/media, and hands it directly to the Hub.

### The Core Engine (`core/`)
- **`core/state.py`**: **The Source of Truth.** Contains the Pydantic models for `TaskState`. This is the shared memory object passed between the Hub and all Spokes.
- **`core/hub/orchestrator.py`**: **The Manager.** Examines `TaskState` and routes requests to the correct Spoke via the registry. 
- **`core/spokes/base.py`**: **The Blueprint.** Defines `class Spoke(ABC)`. Every agent inherits from this, enforcing that they all have a standard `.run()` method.
- **`core/llm/router.py`**: **The Gateway.** The ONLY file allowed to talk to OpenAI/Anthropic. It reads `models.yaml` to route requests, handles Langfuse tracing, and enforces the Pydantic structured outputs.

### The Workers (`agents/`)
- **`agents/*.py`** (e.g., `caption_agent.py`): The individual workers. They subclass `Spoke`, define their specific Pydantic schemas, load their prompt from the `prompts/` folder, and call `router.py`.
- **`agents/registry.py`**: A simple dictionary that registers all Spokes so the Hub can dynamically call them by name (e.g., `registry["caption_agent"]`).

### The Utilities (`tools/` & `configs/`)
- **`tools/`**: Pure Python functions (no LLM logic). Includes interacting with Meta APIs, saving client data, or sending WhatsApp replies. Called by Spokes or the Hub.
- **`configs/models.yaml`**: Configuration file that defines which physical LLM (e.g., `gpt-4o`, `claude-3-5-sonnet`) is mapped to the `ACTIVE_MODEL` environment variable.

---

## 4. The Data Flow (A message's lifecycle)

1. **Inbound**: A WhatsApp message hits `interfaces/whatsapp.py`.
2. **State Load**: The user's historical session is loaded into a `TaskState` object.
3. **Hub Routing**: The Hub (`orchestrator.py`) analyzes the message and the `TaskState` to determine which Spoke is needed.
4. **Execution**: The Hub calls the selected Spoke (e.g., `CaptionSpoke.run(state)`).
5. **LLM Generation**: The Spoke calls `core/llm/router.py` with its specific prompt and Pydantic schema. Langfuse silently logs this transaction.
6. **State Update**: The Spoke returns a strict object. The Hub updates the `TaskState`.
7. **Outbound**: The Hub determines the final text reply, and `interfaces/whatsapp.py` sends it back via the WhatsApp API.

---

## 5. How to Add a New Spoke

When you need Jarvis to learn a new skill (e.g., `ResearchAgent`), follow these exact steps:

1. **Create the Prompt**: Write the system instructions in `prompts/research_agent.md`.
2. **Create the File**: Create `agents/research_agent.py`.
3. **Write the Class**:
   - Inherit from `Spoke` (from `core.spokes.base`).
   - Define your strict Pydantic structures: `class Input(BaseModel)` and `class Output(BaseModel)`.
   - Implement the `async def run(self, state: TaskState, ...)` method to call `router.py`.
4. **Register It**: Open `agents/registry.py` and import/add your new `ResearchSpoke` to the registry mapping.
5. **Update Hub**: If necessary, update the logic in `core/hub/orchestrator.py` so it knows *when* to route traffic to your new Spoke.
