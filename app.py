import os
import sys
import solar_server
from pathlib import Path
import gradio as gr
from dotenv import load_dotenv
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SERVER_PATH = str(BASE_DIR / "solar_server.py")

agent_name = "Solar Yield and LCOE Estimation Assistant"
instructions = "You are able to estimate solar yield and LCOE for a given location of a utility scale solar pv project. Answer only using estimate_utility_solar_yield_and_lcoe tool."
model = "gpt-5.4-mini"
message = "Evaluate a 100 MW single-axis tracking solar project near Barstow, CA (34.89° N, -117.02° W). Assume $1,050/kW CapEx, 7% WACC, and 25-year project life. Give me annual yield (MWh), capacity factor, and LCOE ($/MWh). Now compare LCOE across three ITC scenarios: 0% (unsubsidized), 30% base ITC, and 40% with the Energy Community / Domestic Content adder."

async def chat_with_agent(message: str, history: list[dict]) -> str:
    # Spawn solar_server.py over stdio and wrap it into the OpenAI Agent
    async with MCPServerStdio(name="Solar Yield and LCOE Estimator Server", 
    params = {"command": sys.executable, "args": [SERVER_PATH], "env": os.environ.copy(), "cwd": str(BASE_DIR),}, 
    client_session_timeout_seconds=60) as mcp_server:
        agent = Agent(name=agent_name, instructions=instructions, model=model, mcp_servers=[mcp_server],)
        result = await Runner.run(agent, message)
        return result.final_output

demo = gr.ChatInterface(
    fn=chat_with_agent,
    title="Solar Intelligence",
    description="Estimate solar yield and LCOE for a given location of a utility scale solar pv project. The agent spawns the MCP server and invokes its tools.",
    examples=[
        message,
    ],
)

if __name__ == "__main__":
    demo.launch()
