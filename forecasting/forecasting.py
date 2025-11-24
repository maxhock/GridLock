from typing import List
from dataclasses import dataclass
import requests
import os


@dataclass
class ForecastInput:
    """Input data for forecasting"""

    current_loads: List[float]  # Current house loads [MW]
    current_time: float  # Simulation time [seconds]
    time_step: float  # Time step size [seconds]

    def to_dict(self):
        """Convert to dictionary for JSON serialization"""
        return {
            "current_loads": self.current_loads,
            "current_time": self.current_time,
            "time_step": self.time_step,
        }


@dataclass
class ForecastOutput:
    total_load_forecast: float  # Predicted total load [MW]
    predict_time: float


def predict_agg_load(forecast_input: ForecastInput) -> ForecastOutput:

    forecast_output = fast_api(forecast_input)

    return forecast_output


def fast_api(forecast_input: ForecastInput) -> ForecastOutput:
    """Call external FastAPI server for forecasting"""

    api_host = os.getenv("FASTAPI_HOST", "fastapi_server")  # Default to container name
    api_port = os.getenv("FASTAPI_PORT", "8000")  # Default port
    api_url = f"http://{api_host}:{api_port}/forecast"

    # Prepare data for your API
    api_data = {"numbers": forecast_input.current_loads}

    try:
        # Call your FastAPI server
        print(f"Calling FastAPI with {len(forecast_input.current_loads)} loads")
        response = requests.post(
            api_url,  # "http://fastapi-server:8000/forecast",  # Container name
            json=api_data,
            timeout=10,
        )

        if response.status_code == 200:
            result = response.json()
            total_sum = result["sum"]

            print(f"Server Prediction: {total_sum:.6f}")

            return ForecastOutput(
                total_load_forecast=total_sum,
                predict_time=forecast_input.current_time + forecast_input.time_step,
            )
        else:
            print(f"FastAPI error: HTTP {response.status_code}")
            return _fallback_forecast(forecast_input)

    except Exception as e:
        print(f"FastAPI call failed: {e}")
        return _fallback_forecast(forecast_input)


def _fallback_forecast(forecast_input: ForecastInput) -> ForecastOutput:
    """Fallback if API is unavailable"""
    print("🔄 Using fallback: simple sum")
    total = sum(forecast_input.current_loads)
    return ForecastOutput(
        total_load_forecast=total,
        predict_time=forecast_input.current_time + forecast_input.time_step,
    )
