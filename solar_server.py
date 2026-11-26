from fastmcp import FastMCP
from backend.solar import (
    SolarSimulationInput,
    SolarSimulationOutput,
    estimate_utility_solar_yield_and_lcoe as run_solar_estimate,
)

mcp = FastMCP("solar_server")

@mcp.tool()
async def estimate_utility_solar_yield_and_lcoe(request: SolarSimulationInput) -> SolarSimulationOutput:
    """Estimate the utility-scale solar yield and LCOE for a given location."""
    return run_solar_estimate(request)

if __name__ == "__main__":
    mcp.run(transport="stdio")
