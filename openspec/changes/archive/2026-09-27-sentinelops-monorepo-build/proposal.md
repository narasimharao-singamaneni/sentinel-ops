# Proposal: SentinelOps Monorepo Build Plan

## Agent Execution Rules
1. Process tasks sequentially from top to bottom. Do NOT skip tasks.
2. Execute **only ONE task at a time**.
3. Use **strictly `uv`** commands for package management, script execution, and test execution (NEVER use `pip` or Docker).
4. After writing or modifying code for a task, run the specified **Verification Command**.
5. Both Positive (+v) and Negative (-v) assertions inside test files MUST pass.
6. Do NOT mark a task as complete `[x]` until its verification command succeeds with zero errors.
