from abc import ABC, abstractmethod
from typing import ClassVar
from pydantic import BaseModel

class Spoke(ABC):
    name: ClassVar[str]
    description: ClassVar[str]  # hub reads this to decide when to call you
    model_role: ClassVar[str]

    class Input(BaseModel):
        pass

    class Output(BaseModel):
        error: str | None = None

    @abstractmethod
    async def run(self, input: "Spoke.Input") -> "Spoke.Output":
        ...

    def to_tool_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.Input.model_json_schema(),
            }
        }