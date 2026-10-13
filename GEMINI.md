# Autonomous Agent Behavior Rules

## Tool Execution & Question Policy
- **Act Autonomously**: Execute tools, scripts, and commands directly without stopping to ask user confirmation unless a destructive action (like deleting git history or databases) is involved.
- **Avoid Question Spikes**: Do NOT use `ask_question` for basic decisions, standard defaults, or routine step-by-step progress. Proceed with the most sensible choice and report the outcome.
- **Peerer & CLI Execution**: Run commands and delegate tasks directly using their full paths without prompting the user.
