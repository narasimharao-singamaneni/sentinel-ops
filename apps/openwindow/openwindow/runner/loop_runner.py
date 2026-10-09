import time
import sys
from pathlib import Path
# Add project root to PYTHONPATH
sys.path.append(str(Path(__file__).resolve().parents[1]))

from openwindow.engine.executor import evaluate_execution_cycle
from openwindow.config.settings import POLL_INTERVAL_SECONDS, USER_ID
from openwindow.config.logger import get_logger

logger = get_logger(__name__)

def main():
    logger.info("Starting OpenWindow loop...")

    try:
        while True:
            logger.info("\n===== New Cycle =====")

            evaluate_execution_cycle(user_id=USER_ID)

            logger.info("Waiting for next cycle...\n")
            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("\nStopping OpenWindow gracefully...")


if __name__ == "__main__":
    main()