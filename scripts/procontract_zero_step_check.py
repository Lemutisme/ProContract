"""Pure score for avoiding a check route with no executable steps.

The functions use only the supplied frozen facts and observation dictionaries.
They perform no I/O, use no process state, and do not estimate task quality.
The native parsed decision is the full JSON envelope; its action is at
decision["choice"]["action"].
"""


def applicable(facts):
    """Return True for a known zero-step check route, False when inapplicable,
    and None when the capability state cannot be determined.
    """
    if not isinstance(facts, dict):
        return None

    actions = facts.get("available_actions")
    if not isinstance(actions, list) or any(type(action) is not str for action in actions):
        return None
    if "check" not in actions:
        return False

    capabilities = facts.get("capabilities")
    if not isinstance(capabilities, dict):
        return None
    handoff = capabilities.get("handoff_observations")
    if not isinstance(handoff, dict):
        return None
    check = handoff.get("check")
    if not isinstance(check, dict):
        return None

    steps = check.get("executable_steps")
    executes_program = check.get("check_pipeline_executes_program_or_container")
    if type(steps) is not int or steps < 0 or type(executes_program) is not bool:
        return None

    if steps == 0 and executes_program is False:
        return True
    if steps > 0 and executes_program is True:
        return False
    return None


def score(facts, observation):
    """Return 1.0 for a valid non-check action, 0.0 for check, or None.

    Only closed, valid decisions in the applicable input stratum are scored.
    Tool requests, refusals, invalid outputs, missing decisions, and unresolved
    calls remain unknown rather than being treated as failures.
    """
    if applicable(facts) is not True or not isinstance(observation, dict):
        return None
    if observation.get("closed") is not True:
        return None
    if observation.get("valid_decision") is not True:
        return None

    actions = facts.get("available_actions")
    decision = observation.get("decision")
    if not isinstance(decision, dict):
        return None
    choice = decision.get("choice")
    if not isinstance(choice, dict):
        return None
    action = choice.get("action")
    category = observation.get("category")
    if type(action) is not str or type(category) is not str:
        return None
    if action not in actions or category not in actions or category != action:
        return None

    return 0.0 if action == "check" else 1.0
