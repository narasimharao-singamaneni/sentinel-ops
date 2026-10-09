from fastapi import FastAPI
from openwindow.engine.executor import evaluate_execution_cycle
from openwindow.config.settings import USER_ID

app = FastAPI()


@app.get("/run-check")
def run_check():
    result = evaluate_execution_cycle(user_id=USER_ID)
    return result