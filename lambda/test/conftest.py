"""
Mocks de ask_sdk_core pour permettre l'import de lambda_function.py en CI
sans installer la lib (lourde + spécifique runtime Lambda Alexa-hosted).

Les tests qui ont besoin du vrai SDK doivent skipper si non installé.
"""

import sys
import types
from unittest.mock import MagicMock


def _make_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_ask_sdk_mocks():
    """Si ask_sdk_core n'est pas installé, monte des stubs minimaux."""
    try:
        import ask_sdk_core  # noqa: F401
        return  # vrai SDK dispo, on n'écrase pas
    except ImportError:
        pass

    # Modules requis par les imports du Lambda
    pkgs = [
        "ask_sdk_core",
        "ask_sdk_core.dispatch_components",
        "ask_sdk_core.skill_builder",
        "ask_sdk_core.utils",
        "ask_sdk_core.handler_input",
        "ask_sdk_core.attributes_manager",
        "ask_sdk_model",
        "ask_sdk_model.slu",
        "ask_sdk_model.slu.entityresolution",
    ]
    for p in pkgs:
        _make_module(p)

    # Classes stubs — héritage neutre
    class _Stub:
        def __init__(self, *a, **kw): pass
        def __call__(self, *a, **kw): return self
        def __getattr__(self, _): return _Stub()

    sys.modules["ask_sdk_core.dispatch_components"].AbstractRequestHandler = _Stub
    sys.modules["ask_sdk_core.dispatch_components"].AbstractRequestInterceptor = _Stub
    sys.modules["ask_sdk_core.dispatch_components"].AbstractExceptionHandler = _Stub
    sys.modules["ask_sdk_core.skill_builder"].SkillBuilder = _Stub

    utils = sys.modules["ask_sdk_core.utils"]
    utils.is_intent_name = lambda name: (lambda hi: getattr(hi, "_intent_name", None) == name)
    utils.is_request_type = lambda name: (lambda hi: getattr(hi, "_request_type", None) == name)
    utils.get_slot_value = lambda hi, slot_name: getattr(hi, "_slots", {}).get(slot_name)
    utils.get_slot = lambda hi, slot_name: getattr(hi, "_slot_objects", {}).get(slot_name)
    utils.get_intent_name = lambda hi: getattr(hi, "_intent_name", None)
    utils.get_account_linking_access_token = lambda hi: getattr(hi, "_access_token", None)

    handler_input = sys.modules["ask_sdk_core.handler_input"]
    handler_input.HandlerInput = _Stub

    er = sys.modules["ask_sdk_model.slu.entityresolution"]
    class _StatusCode:
        ER_SUCCESS_MATCH = "ER_SUCCESS_MATCH"
    er.StatusCode = _StatusCode

    # ask_sdk_model — SessionEndedReason
    class _SessionEndedReason:
        ERROR = "ERROR"
        EXCEEDED_MAX_REPROMPTS = "EXCEEDED_MAX_REPROMPTS"
        USER_INITIATED = "USER_INITIATED"
    sys.modules["ask_sdk_model"].SessionEndedReason = _SessionEndedReason

    # ask_sdk_model.interfaces.display — HintDirective / PlainTextHint
    for p in ["ask_sdk_model.interfaces", "ask_sdk_model.interfaces.display"]:
        _make_module(p)
    sys.modules["ask_sdk_model.interfaces.display"].HintDirective = _Stub
    sys.modules["ask_sdk_model.interfaces.display"].PlainTextHint = _Stub


_ensure_ask_sdk_mocks()
