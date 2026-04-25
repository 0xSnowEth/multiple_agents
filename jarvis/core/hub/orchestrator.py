import json
from core.llm.router import chat

MAX_TURNS = 25

async def run_hub(
    *,
    user_message: str,
    spokes: list,
    system_prompt: str,
) -> str:
    spoke_map = {s.name: s for s in spokes}
    tools = [s.to_tool_schema() for s in spokes]

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    for turn in range(MAX_TURNS):
        response = await chat(role="hub", messages=messages, tools=tools)
        msg = response.choices[0].message

        # no tool call = final answer
        if not msg.tool_calls:
            return msg.content

        # append assistant turn
        messages.append(msg)

        # dispatch each tool call to the matching spoke
        for tool_call in msg.tool_calls:
            spoke_name = tool_call.function.name
            spoke = spoke_map.get(spoke_name)

            if not spoke:
                result = f"Error: spoke '{spoke_name}' not found"
            else:
                args = json.loads(tool_call.function.arguments)
                output = await spoke.run(spoke.Input(**args))
                result = output.model_dump_json()

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

    raise RuntimeError(f"Hub exceeded MAX_TURNS={MAX_TURNS}")