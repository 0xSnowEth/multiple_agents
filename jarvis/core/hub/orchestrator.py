import json
from core.llm.router import chat
from core.state import TaskState
from agents.registry import AGENT_REGISTRY
from prompts import HUB_SYSTEM_PROMPT

MAX_TURNS = 25

async def run_hub(user_message: str, state: TaskState) -> TaskState:
    spokes = AGENT_REGISTRY
    spoke_map = {s.name: s for s in spokes}
    tools = [s.to_tool_schema() for s in spokes]
    
    execute_action_tool = {
        "type": "function",
        "function": {
            "name": "execute_pending_action",
            "description": "Call this ONLY when the operator explicitly says 'yes', 'send it', 'approve', or confirms they want to execute a draft or post. This will instantly execute the action via the Meta API.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {"type": "string", "enum": ["send_approval", "send_payment", "send_lead_response", "post_to_page"]},
                    "recipient_number": {"type": "string", "description": "WhatsApp number to send to, OR the client slug for posting (e.g. 'tesla')"},
                    "from_number": {"type": "string", "enum": ["rafi_primary", "rafi_billing", "meta_graph_api"]},
                    "message": {"type": "string", "description": "The exact message to send, or the caption if posting"},
                    "post_platforms": {"type": "array", "items": {"type": "string"}, "description": "List of platforms if posting (e.g. ['instagram', 'facebook'])"},
                    "post_image_url": {"type": "string", "description": "Publicly accessible image URL if posting to Instagram"}
                },
                "required": ["action_type", "recipient_number", "from_number", "message"]
            }
        }
    }
    tools.append(execute_action_tool)

    if not state.conversation_history:
        state.conversation_history = []
        
    state.conversation_history.append({"role": "user", "content": user_message})

    from tools.client_store import list_clients
    known_clients = await list_clients()
    client_names = [c.get("name") for c in known_clients] if known_clients else ["None"]
    
    dynamic_prompt = f"{HUB_SYSTEM_PROMPT}\n\n### CURRENT KNOWN CLIENTS IN DATABASE:\n{', '.join(client_names)}"
    
    messages = [{"role": "system", "content": dynamic_prompt}]
    messages.extend(state.conversation_history[-10:])

    for turn in range(MAX_TURNS):
        response = await chat(role="hub", messages=messages, tools=tools)
        msg = response.choices[0].message

        # no tool call = final answer
        if not msg.tool_calls:
            state.pending_reply = msg.content
            state.conversation_history.append({"role": "assistant", "content": msg.content})
            return state

        # append assistant turn
        messages.append(msg)

        # dispatch each tool call to the matching spoke
        for tool_call in msg.tool_calls:
            spoke_name = tool_call.function.name
            
            if spoke_name == "execute_pending_action":
                from core.state import PendingAction
                args = json.loads(tool_call.function.arguments)
                state.pending_action = PendingAction(**args)
                if args.get("action_type") == "post_to_page":
                    state.pending_action.post_client_id = args.get("recipient_number")
                state.status = "done"
                state.pending_reply = f"✅ Action locked in. Executing {args.get('action_type')}..."
                state.conversation_history.append({"role": "assistant", "content": state.pending_reply})
                return state

            spoke = spoke_map.get(spoke_name)

            if not spoke:
                result = f"Error: spoke '{spoke_name}' not found"
            else:
                args = json.loads(tool_call.function.arguments)
                output = await spoke.run(spoke.Input(**args))
                result = output.model_dump_json()
                
                # Save spoke output into state so whatsapp.py can use it
                try:
                    state.spoke_result = json.loads(result)
                except Exception:
                    pass

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    state.error_message = f"Hub exceeded MAX_TURNS={MAX_TURNS}"
    state.pending_reply = "⚠️ I encountered an error (exceeded max thinking steps)."
    return state