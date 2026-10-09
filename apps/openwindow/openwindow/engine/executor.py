"""
This module defines the execution coordinator for OpenWindow.

The executor is responsible for progressing the system through the
documented execution flow by invoking the appropriate components
and enforcing execution boundaries.

It does not perform data fetching, persistence, explanation generation,
or notification delivery.
"""

import json
from random import random, choice
import time
import requests
from openwindow.engine.data_models import ExecutionData
from openwindow.config.settings import POLL_INTERVAL_SECONDS, USER_ID
from openwindow.config.logger import get_logger
from openwindow.ai.llm_client import generate_explanation

logger = get_logger(__name__)

NO_ACTION = object()

def evaluate_execution_cycle(user_id):
    data = ExecutionData()
    data = _load_configuration_and_state(user_id, data)
    data = _request_availability(data)
    data = _evaluate_availability_change(data)
    data = _exit_if_no_change(data)
    if data is NO_ACTION:
        return {
            "change_detected": False,
            "change_type": None,
            "priority": None,
            "explanation": None,
            "action": None,
            "urgency": None,
            "message": "No change detected"
        }
    logger.info("Change detected → continuing execution")
    data = _build_context(data)
    data = _generate_explanation(data)
    data = _deliver_alert(data)
    _record_alert_and_state(data)
    return {
    "change_detected": True,
    "change_type": data.change_type,
    "priority": data.priority,
    "explanation": data.explanation,
    "action": data.action_hint,
    "urgency": data.urgency,
    "message": "Change detected"
    }

#Ownership: Configuration & State (read by Execution Engine)
def _load_configuration_and_state(user_id, data: ExecutionData) -> ExecutionData:
    with open("state.json", "r") as f:
        data.state = json.load(f)
    return data

#Ownership: Data Providers (invoked by Execution Engine)
def _request_availability(data: ExecutionData) -> ExecutionData:
    try:
        with open("availability.json", "r") as f:
            availability_data = json.load(f)
            data.availability = choice(availability_data).get("availability", "UNKNOWN")
            data.park = choice(availability_data).get("park", "UNKNOWN")
            data.date = choice(availability_data).get("date", "UNKNOWN")
            logger.info(f"Fetched availability: {data.availability} for park: {data.park} on date: {data.date}")
        return data

    except Exception as e:
        logger.error("Error fetching data: %s", e)
        return NO_ACTION  # skip this cycle safely

    return data

#Ownership: Execution Engine (comparison using Configuration & State only)
def _evaluate_availability_change(data: ExecutionData) -> ExecutionData:
    previous = data.state["last_availability"]
    data.previous_availability = previous
    current = data.availability

    data.change_detected = (previous != current)
    logger.info(f"Previous: {previous}")
    logger.info(f"Current: {current}")
    # NEW: classify change type
    if data.change_detected:
        if previous == "CLOSED" and current == "OPEN":
            data.change_type = "OPENED"
            data.priority = "HIGH"
        elif previous == "OPEN" and current == "CLOSED":
            data.change_type = "CLOSED"
            data.priority = "LOW"
    return data

#Ownership: Execution Engine (decision based on Configuration & State)
def _exit_if_no_change(data: ExecutionData) -> ExecutionData:
    if not data.change_detected:
        return NO_ACTION
    return data  # still placeholder

#Ownership: Context Builder (invoked by Execution Engine)
def _build_context(data: ExecutionData) -> ExecutionData:
    logger.info("Building context...")
    data.context = "Sample context"
    return data

#Ownership: Explanation Engine (invoked by Execution Engine)
def _generate_explanation(data: ExecutionData) -> ExecutionData:
    parsed = generate_explanation(
        data.previous_availability,
        data.availability,
        data.priority,
        data.park,
        data.date
    )
    data.explanation = parsed.get("reason", "No explanation provided")
    data.action_hint = parsed.get("action", "No action provided")
    if data.priority == "HIGH":
        data.urgency = "HIGH"
    elif data.priority == "LOW":
        data.urgency = "LOW"

    return data

#Ownership: Alert Delivery (invoked by Execution Engine)
def _deliver_alert(data: ExecutionData) -> ExecutionData:
    data.alert = f"ALERT: {data.explanation}"
    logger.info("ALERT TRIGGERED")
    logger.info(data.alert)
    return data

#Ownership: Configuration & State (written by Execution Engine)
def _record_alert_and_state(data: ExecutionData) -> None:
    # update in memory
    data.state["last_availability"] = data.availability
    # persist to file
    with open("state.json", "w") as f:
        json.dump(data.state, f, indent=2)

    logger.info("State persisted → last_availability = %s", data.state["last_availability"])


def main():
    try:
        logger.info("Starting OpenWindow loop...")
        while True:
            logger.info("\n===== New Cycle =====")
            evaluate_execution_cycle(user_id=USER_ID)
            logger.info("Waiting for next cycle...\n")
            time.sleep(POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("\nStopping OpenWindow gracefully...")

if __name__ == "__main__":
    main()