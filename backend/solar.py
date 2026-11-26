import pandas as pd
from pydantic import BaseModel, Field
import pvlib
from pvlib.location import Location
from pvlib.pvsystem import PVSystem, Array, SingleAxisTrackerMount
from pvlib.modelchain import ModelChain
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS


# =====================================================================
# 1. Pydantic Schemas (Input & Output)
# =====================================================================

class SolarSimulationInput(BaseModel):
    """Input parameters for utility-scale solar yield and LCOE simulation."""
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Site latitude in decimal degrees.")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Site longitude in decimal degrees.")
    dc_capacity_mw: float = Field(default=50.0, gt=0.0, description="Total DC nameplate capacity in MW.")
    dc_ac_ratio: float = Field(default=1.30, gt=0.5, le=2.5, description="Inverter loading ratio (DC/AC).")
    capex_per_kw_dc: float = Field(default=950.0, ge=0.0, description="Installed capital cost in USD per kWdc.")
    opex_per_kw_dc_year: float = Field(default=15.0, ge=0.0, description="Annual O&M cost in USD per kWdc-year.")
    discount_rate: float = Field(default=0.065, gt=0.0, lt=1.0, description="Discount rate / WACC (e.g., 0.065 for 6.5%).")
    project_lifetime_years: int = Field(default=30, ge=1, le=50, description="Operational lifespan in years.")
    degradation_rate: float = Field(default=0.005, ge=0.0, lt=0.1, description="Annual module degradation rate (e.g., 0.005 for 0.5%).")


class SiteCoordinates(BaseModel):
    latitude: float
    longitude: float


class FinancialAssumptions(BaseModel):
    capex_per_kw_dc: float
    opex_per_kw_dc_year: float
    discount_rate: float
    lifetime_years: int
    annual_degradation: float


class SolarSimulationOutput(BaseModel):
    """Structured simulation output containing technical yield and LCOE metrics."""
    site: SiteCoordinates
    system_size_mwdc: float
    system_size_mwac: float
    year1_energy_mwh: float
    specific_yield_kwh_kwp: float
    ac_capacity_factor_percent: float
    lcoe_usd_per_mwh: float
    financial_assumptions: FinancialAssumptions
    monthly_generation_mwh: dict[str, float]


# =====================================================================
# 2. Modular Helper Functions
# =====================================================================

def fetch_tmy_weather(latitude: float, longitude: float) -> pd.DataFrame:
    """
    Fetches Typical Meteorological Year (TMY) hourly irradiance and weather data
    from the free PVGIS database (pvlib v0.11+ 2-tuple return format).

    Args:
        latitude: Site latitude in decimal degrees.
        longitude: Site longitude in decimal degrees.

    Returns:
        Hourly weather DataFrame formatted with pvlib standard variable names.
    """
    weather, _ = pvlib.iotools.get_pvgis_tmy(
        latitude=latitude,
        longitude=longitude,
        map_variables=True
    )
    return weather


def build_utility_tracker_system(dc_capacity_mw: float, dc_ac_ratio: float) -> PVSystem:
    """
    Constructs a standard utility-scale single-axis tracker PVSystem using 
    pvlib v0.11+ Array architecture and industry-standard loss assumptions.

    Args:
        dc_capacity_mw: Total DC nameplate capacity in megawatts.
        dc_ac_ratio: Ratio of DC module capacity to AC inverter capacity.

    Returns:
        Configured pvlib PVSystem object ready for simulation.
    """
    mount = SingleAxisTrackerMount(
        axis_tilt=0,
        axis_azimuth=180,
        max_angle=60,
        backtrack=True,
        gcr=0.35
    )

    dc_watts = dc_capacity_mw * 1e6
    ac_watts = dc_watts / dc_ac_ratio

    default_losses = {
        'soiling': 2.0,
        'shading': 1.0,
        'snow': 0.0,
        'mismatch': 2.0,
        'wiring': 2.0,
        'connections': 0.5,
        'lid': 1.5,
        'nameplate_rating': 1.0,
        'age': 0.0,
        'availability': 3.0
    }

    array = Array(
        mount=mount,
        module_parameters={'pdc0': dc_watts, 'gamma_pdc': -0.0035},
        temperature_model_parameters=TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_glass']
    )

    return PVSystem(
        arrays=[array],
        inverter_parameters={'pdc0': ac_watts},
        losses_parameters=default_losses
    )


def run_energy_simulation(system: PVSystem, site: Location, weather: pd.DataFrame) -> pd.Series:
    """
    Executes the pvlib ModelChain to compute hourly AC power output over a full year.

    Args:
        system: Configured PVSystem object.
        site: pvlib Location object defining site coordinates.
        weather: Hourly TMY weather DataFrame.

    Returns:
        Hourly AC energy generation time series in MWh.
    """
    mc = ModelChain(
        system=system,
        location=site,
        aoi_model='ashrae',
        spectral_model='no_loss',
        losses_model='pvwatts'
    )
    mc.run_model(weather)
    return mc.results.ac / 1e6  # Convert Watts (hourly) to MWh


def calculate_lcoe(
    year1_energy_mwh: float,
    dc_capacity_mw: float,
    capex_per_kw_dc: float,
    opex_per_kw_dc_year: float,
    discount_rate: float,
    lifetime_years: int,
    degradation_rate: float
) -> float:
    """
    Calculates unsubsidized Levelized Cost of Energy (LCOE) in $/MWh using 
    discounted cash flow (DCF) over the project's operational lifetime.

    Args:
        year1_energy_mwh: Total AC electricity generated in Year 1 (MWh).
        dc_capacity_mw: Total DC nameplate capacity (MW).
        capex_per_kw_dc: Upfront capital cost ($/kWdc).
        opex_per_kw_dc_year: Annual operations and maintenance cost ($/kWdc-yr).
        discount_rate: Weighted Average Cost of Capital (decimal, e.g., 0.065).
        lifetime_years: Operational lifespan of the plant in years.
        degradation_rate: Annual panel output degradation rate (decimal, e.g., 0.005).

    Returns:
        Levelized Cost of Energy in USD per MWh.
    """
    dc_capacity_kw = dc_capacity_mw * 1000.0
    npv_costs = dc_capacity_kw * capex_per_kw_dc
    annual_opex = dc_capacity_kw * opex_per_kw_dc_year
    npv_energy_mwh = 0.0

    for year in range(1, lifetime_years + 1):
        discount_factor = (1.0 + discount_rate) ** year
        npv_costs += annual_opex / discount_factor

        degraded_mwh = year1_energy_mwh * ((1.0 - degradation_rate) ** (year - 1))
        npv_energy_mwh += degraded_mwh / discount_factor

    return npv_costs / npv_energy_mwh


# =====================================================================
# 3. Main Orchestrator (MCP Tool Entrypoint)
# =====================================================================

def estimate_utility_solar_yield_and_lcoe(params: SolarSimulationInput) -> SolarSimulationOutput:
    """
    End-to-end orchestrator that simulates a utility-scale single-axis tracker 
    solar plant and estimates technical yield and LCOE.

    Args:
        params: Validated SolarSimulationInput parameters.

    Returns:
        Structured SolarSimulationOutput model ready for JSON serialization.
    """
    # 1. Fetch weather & build physical models
    weather = fetch_tmy_weather(params.latitude, params.longitude)
    site = Location(latitude=params.latitude, longitude=params.longitude)
    system = build_utility_tracker_system(params.dc_capacity_mw, params.dc_ac_ratio)

    # 2. Run 8,760-hour physics simulation
    ac_hourly_mwh = run_energy_simulation(system, site, weather)

    # 3. Compute technical KPIs
    year1_mwh = float(ac_hourly_mwh.sum())
    dc_capacity_kw = params.dc_capacity_mw * 1000.0
    ac_capacity_mw = params.dc_capacity_mw / params.dc_ac_ratio

    specific_yield = (year1_mwh * 1000.0) / dc_capacity_kw
    capacity_factor = (year1_mwh / (ac_capacity_mw * 8760.0)) * 100.0
    monthly_mwh = {
        idx.strftime('%b'): round(float(val), 1)
        for idx, val in ac_hourly_mwh.resample('ME').sum().items()
    }

    # 4. Compute financial LCOE
    lcoe = calculate_lcoe(
        year1_energy_mwh=year1_mwh,
        dc_capacity_mw=params.dc_capacity_mw,
        capex_per_kw_dc=params.capex_per_kw_dc,
        opex_per_kw_dc_year=params.opex_per_kw_dc_year,
        discount_rate=params.discount_rate,
        lifetime_years=params.project_lifetime_years,
        degradation_rate=params.degradation_rate
    )

    return SolarSimulationOutput(
        site=SiteCoordinates(latitude=params.latitude, longitude=params.longitude),
        system_size_mwdc=params.dc_capacity_mw,
        system_size_mwac=round(ac_capacity_mw, 2),
        year1_energy_mwh=round(year1_mwh, 1),
        specific_yield_kwh_kwp=round(specific_yield, 1),
        ac_capacity_factor_percent=round(capacity_factor, 2),
        lcoe_usd_per_mwh=round(lcoe, 2),
        financial_assumptions=FinancialAssumptions(
            capex_per_kw_dc=params.capex_per_kw_dc,
            opex_per_kw_dc_year=params.opex_per_kw_dc_year,
            discount_rate=params.discount_rate,
            lifetime_years=params.project_lifetime_years,
            annual_degradation=params.degradation_rate
        ),
        monthly_generation_mwh=monthly_mwh
    )


if __name__ == "__main__":
    request = SolarSimulationInput(latitude=31.9973, longitude=-102.0779)
    response = estimate_utility_solar_yield_and_lcoe(request)
    print(response.model_dump_json(indent=2))
