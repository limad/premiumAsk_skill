# VERSION 0.12.0

CODE_VERS = 0.3
TOKEN = "" # ADD YOUR LONG LIVED TOKEN IF NEEDED OTHERWISE LEAVE BLANK
CAN_POST = True

""" NO NEED TO EDIT ANYTHING UNDER THE LINE """
# Built-In Imports
import logging
import json
from typing import Union, Optional

# 3rd-Party Imports
import isodate
import urllib3
from ask_sdk_core.dispatch_components import AbstractExceptionHandler
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.dispatch_components import AbstractRequestInterceptor
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.utils import (
    get_account_linking_access_token,
    is_request_type,
    is_intent_name,
    get_intent_name,
    get_slot,
    get_slot_value,
)
from ask_sdk_model import SessionEndedReason
from ask_sdk_model.slu.entityresolution import StatusCode
from urllib3 import HTTPResponse

# Local Imports
import prompts
from schemas import QuestionState, QuestionStateError
from const import (
    INPUT_TEXT_ENTITY,
    RESPONSE_YES,
    RESPONSE_NO,
    RESPONSE_NONE,
    RESPONSE_SELECT,
    RESPONSE_NUMERIC,
    RESPONSE_DURATION,
    RESPONSE_STRING,
    RESPONSE_DATE_TIME,
)
from config import (JEEDOM_URL, APIKEY, DEBUG, VERIFY_SSL)
QUESTION_URL = f"plugins/alexaapiv2/core/php/askQuestion.php?apikey={APIKEY}"
REPONSE_URL = f"plugins/alexaapiv2/core/php/askResponse.php?apikey={APIKEY}&command=reponseASK"
POST_LOG_URL = f"plugins/alexaapiv2/core/php/askResponse.php?apikey={APIKEY}&command=log"
JEEDOM_URL = JEEDOM_URL.rstrip("/")

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)


def _handle_response(handler, speak_out: Optional[str]):
    """
    This function has the purpose of allowing the suspension of the default Okay response
    so the user can have Jeedom do a custom response or follow-up question.

    Fixed issue: #147

    :param handler:
    :param speak_out:
    :return:
    """
    if speak_out:
        return handler.response_builder.speak(speak_out).response
    return handler.response_builder.response


class Borg:
    """Borg MonoState Class for State Persistence."""

    _shared_state = {}

    def __init__(self):
        self.__dict__ = self._shared_state


def _init_http_pool():
    return urllib3.PoolManager(
        cert_reqs="CERT_REQUIRED" if VERIFY_SSL else "CERT_NONE", timeout=urllib3.Timeout(connect=10.0, read=10.0)
    )


def _string_to_bool(value: Optional[str], default: bool = False) -> bool:
    """
    Used because we need to convert boolean values passed in strings since
    entity states don't natively support json and are treated as strings.

    :param value:
    :param default:
    :return:
    """
    if isinstance(value, bool):
        return value

    if not isinstance(value, str):
        return default

    value = value.lower()
    if value == "true":
        return True
    elif value == "false":
        return False

    return default


class JeeAsk(Borg):
    """Jeedom Wrapper Class."""

    jee_state: Optional[Union[QuestionState, QuestionStateError]]

    def __init__(self, handler_input=None):
        Borg.__init__(self)

        # Define class vars
        self.jee_state = None
        self.http = _init_http_pool()

        if handler_input:
            self.handler_input = handler_input

        # Gets data from langua_strings.json file according to the locale
        self.language_strings = self.handler_input.attributes_manager.request_attributes["_"]

        self.token = self._fetch_token() if TOKEN == "" else TOKEN

        self.get_jeeQuestion()

    def _fetch_token(self):
        logger.debug("Fetching Jeedom token from Alexa")
        return get_account_linking_access_token(self.handler_input)

    def _set_jee_error(self, prompt: str):
        """
        Sets the self.jee_state to the error prompt

        Used when a function fails and alexa should say the error message instead of the
        intended one

        :param prompt: Value obtained from prompts file
        :return:
        """
        self.jee_state = QuestionStateError(text=self.language_strings[prompt])

    @staticmethod
    def _build_url(*path: str):
        """
        Builds the url from paths given

        :param path:
        :return:
        """
        logger.debug(f"_build_url:: {JEEDOM_URL}/" + "/".join(path))
        return f"{JEEDOM_URL}/" + "/".join(path)
        
    def _get_headers(self):
        """
        Returns the request headers

        :return:
        """

        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _check_response_errors(self, response: HTTPResponse) -> Union[bool, str]:
        if response.status == 401:
            logger.error("401 Error from Jeedom. Activate debug mode to see more details.")
            logger.debug(f"_check_response_errors => {response.data}")
            #logger.debug(response.data)
            speak_output = "Error 401, " + self.language_strings[prompts.ERROR_401]
            self.post_jee_log(speak_output)
            return speak_output
        if response.status == 404:
            logger.error("404 Error from Jeedom. Activate debug mode to see more details.")
            logger.debug(f"_check_response_errors => {response.data}")
            #logger.debug(response.data)
            speak_output = "Error 404, " + self.language_strings[prompts.ERROR_404]
            self.post_jee_log(speak_output)
            return speak_output
        if response.status >= 400:
            logger.error(f"{response.status} Error from Jeedom. " f"Activate debug mode to see more details.")
            logger.debug(f"_check_response_errors => {response.data}")
            #logger.debug(response.data)
            speak_output = f"Error {response.status}, {self.language_strings[prompts.ERROR_400]}"
            self.post_jee_log(speak_output)
            return speak_output

        return False

    def _get(self, *path: str, extra_headers: Optional[dict] = None):
        """
        Performs a request

        :param path:
        :param headers:
        :param params:
        :return:
        """
        headers = self._get_headers()
        if extra_headers:
            headers = headers.update(extra_headers)

        url = self._build_url(*path)
        logger.debug(f"_get::url:: {url}")
        response = self.http.request("GET", url, headers=headers)

        logger.debug(f"Raw response: {response.data}")

        errors: Union[bool, str] = self._check_response_errors(response)
        if errors:
            self.jee_state = QuestionStateError(text=errors)
            logger.debug(f"_get::jee_state => {self.jee_state}")
            return None

        return response

    def _post(self, *path: str, body: dict, extra_headers: Optional[dict] = None):
        """
        Performs a request

        :param path:
        :param headers:
        :param params:
        :return:
        """
        headers = self._get_headers()
        if extra_headers:
            headers = headers.update(extra_headers)

        url = self._build_url(*path)
        logger.debug(f"_post::url:: {url}")
        response = self.http.request("POST", url, headers=headers, body=json.dumps(body).encode("utf-8"))

        errors: Union[bool, str] = self._check_response_errors(response)
        if errors:
            self.jee_state = QuestionStateError(text=errors)
            logger.debug(f"_post::jee_state => {self.jee_state}")
            return None

        return response

    def _decode_response(self, response) -> Optional[dict]:
        """
        Decodes the response into a json object

        :param response:
        :return: Json object or None
        """
        decoded_response: Union[str, bytes] = json.loads(response.data.decode("utf-8")).get("state")
        logger.debug(f"Decoded response: {decoded_response}")

        if decoded_response:
            return json.loads(decoded_response)

        logger.error(
            "No entity state provided by Jeedom. " "Did you forget to add the actionable notification entity?"
        )
        self._set_jee_error(prompts.ERROR_CONFIG)
        logger.debug(f"_decode_response::jee_state => {self.jee_state}")
        return

    def clear_state(self):
        """
        Clear the state of the local Jeedom object.
        """

        logger.debug("Clearing Jeedom local state")
        self.jee_state = None

    def get_jeeQuestion(self):
        """
        Updates the local Jee state with the servers state

        Used for getting the text to speak, event_id as well as other passable variables
        """
        #response = self._get(f"{QUESTION_URL}", "states", INPUT_TEXT_ENTITY)
        response = self._get(f"{QUESTION_URL}")
        if not response:
            return

        response = self._decode_response(response)
        if not response:
            return
        
        self.jee_state = QuestionState(
            event_id=response.get("event"),
            suppress_confirmation=_string_to_bool(response.get("suppress_confirmation")),
            text=response.get("text"),
            deviceSerialNumber=response.get("deviceSerialNumber"),
            textBrut=response.get("textBrut"),
        )
        logger.debug(f"get_jeeQuestion::jee_state => {self.jee_state}")
            

    def post_jee_event(self, response: str, response_type: str, **kwargs) -> Optional[str]:
        """
        Posts an event to the Jeedom server.

        :param response: The response to send to the Jeedom server.
        :param response_type: The type of response to send to the Jeedom server.
        :param kwargs: Additional parameters to send to the Jeedom server.
        :return: The text to speak to the user.
        """
        body = {
            "event_id": self.jee_state.event_id,
            "event_response": response,
            "event_response_type": response_type,
            "deviceSerialNumber": self.jee_state.deviceSerialNumber,
            "textBrut": self.jee_state.textBrut,
            "code_version": CODE_VERS
        }
        body.update(kwargs)

        if self.handler_input.request_envelope.context.system.person:
            person_id = self.handler_input.request_envelope.context.system.person.person_id
            body["event_person_id"] = person_id

        response = self._post(f"{REPONSE_URL}", body=body)
        if not response:
            return self.jee_state.text

        if not self.jee_state.suppress_confirmation:
            self.clear_state()
            return self.language_strings[prompts.OKAY]

        self.clear_state()
        return ""
        
    def post_jee_log(self, log: str, **kwargs) -> Optional[str]:
        """
        Posts log to the Jeedom server.

        :param response: The response to send to the Jeedom server.
        :param response_type: The type of response to send to the Jeedom server.
        :param kwargs: Additional parameters to send to the Jeedom server.
        :return: The text to speak to the user.
        """
        logger.debug(f"post_jee_log::log: {log}")
        headers = self._get_headers()
        body = {
            "log": log,
            #"event_id": self.jee_state.event_id,
            #"deviceSerialNumber": self.jee_state.deviceSerialNumber,
            "code_version": CODE_VERS
        }
        body.update(kwargs)
        url = f"{JEEDOM_URL}/{POST_LOG_URL}"
        response = self.http.request("POST", url, headers=headers, body=json.dumps(body).encode("utf-8"))
        #response = self._post(f"{POST_LOG_URL}", body=body)
        if not response:
            return
        
        return ""

    def get_value_for_slot(self, slot_name):
        """ "Get value from slot, also known as the (why does amazon make you do this)"""
        slot = get_slot(self.handler_input, slot_name=slot_name)
        if slot and slot.resolutions and slot.resolutions.resolutions_per_authority:
            for resolution in slot.resolutions.resolutions_per_authority:
                if resolution.status.code == StatusCode.ER_SUCCESS_MATCH:
                    for value in resolution.values:
                        if value.value and value.value.name:
                            return value.value.name


class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch."""

    def can_handle(self, handler_input):
        """Check for Launch Request."""
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        """Handler for Skill Launch."""
        jee_obj = JeeAsk(handler_input)
        speak_output: Optional[str] = jee_obj.jee_state.text
        event_id: Optional[str] = jee_obj.jee_state.event_id

        handler = handler_input.response_builder.speak(speak_output)

        if event_id:
            handler.ask("")

        return handler.response


class YesIntentHandler(AbstractRequestHandler):
    """Handler for Yes Intent."""

    def can_handle(self, handler_input):
        """Check for Yes Intent."""
        return is_intent_name("AMAZON.YesIntent")(handler_input)

    def handle(self, handler_input):
        """Handle Yes Intent."""
        logger.info("Yes Intent Handler triggered")
        jee_obj = JeeAsk(handler_input)
        speak_output = jee_obj.post_jee_event(RESPONSE_YES, RESPONSE_YES)

        return _handle_response(handler_input, speak_output)


class NoIntentHandler(AbstractRequestHandler):
    """Handler for No Intent."""

    def can_handle(self, handler_input):
        """Check for No Intent."""
        return is_intent_name("AMAZON.NoIntent")(handler_input)

    def handle(self, handler_input):
        """Handle No Intent."""
        logger.info("No Intent Handler triggered")
        jee_obj = JeeAsk(handler_input)
        speak_output = jee_obj.post_jee_event(RESPONSE_NO, RESPONSE_NO)

        return _handle_response(handler_input, speak_output)


class NumericIntentHandler(AbstractRequestHandler):
    """Handler for Select Intent."""

    def can_handle(self, handler_input):
        """Check for Select Intent."""
        return is_intent_name("Number")(handler_input)

    def handle(self, handler_input):
        """Handle the Select intent."""
        logger.info("Numeric Intent Handler triggered")
        jee_obj = JeeAsk(handler_input)
        number = get_slot_value(handler_input, "Numbers")
        logger.debug(f"Number: {number}")
        if number == "?":
            raise
        speak_output = jee_obj.post_jee_event(number, RESPONSE_NUMERIC)

        return _handle_response(handler_input, speak_output)


class StringIntentHandler(AbstractRequestHandler):
    """Handler for String Intent."""

    def can_handle(self, handler_input):
        """Check for Select Intent."""
        return is_intent_name("String")(handler_input)

    def handle(self, handler_input):
        """Handle String Intent."""
        logger.info("String Intent Handler triggered")
        jee_obj = JeeAsk(handler_input)
        strings = get_slot_value(handler_input, "Strings")
        logger.debug(f"String: {strings}")

        speak_output = jee_obj.post_jee_event(strings, RESPONSE_STRING)

        return _handle_response(handler_input, speak_output)


class SelectIntentHandler(AbstractRequestHandler):
    """Handler for Select Intent."""

    def can_handle(self, handler_input):
        """Check for Select Intent."""
        return is_intent_name("Select")(handler_input)

    def handle(self, handler_input):
        """Handle Select Intent."""
        logger.info("Selection Intent Handler triggered")
        jee_obj = JeeAsk(handler_input)
        selection = jee_obj.get_value_for_slot("Selections")
        logger.debug(f"Selection: {selection}")

        if not selection:
            raise

        jee_obj.post_jee_event(selection, RESPONSE_SELECT)
        data = handler_input.attributes_manager.request_attributes["_"]
        speak_output = data[prompts.SELECTED].format(selection)

        return _handle_response(handler_input, speak_output)


class DurationIntentHandler(AbstractRequestHandler):
    """Handler for Duration Intent."""

    def can_handle(self, handler_input):
        """Check for Duration Intent."""
        return is_intent_name("Duration")(handler_input)

    def handle(self, handler_input):
        """Handle the Duration Intent."""
        logger.info("Duration Intent Handler triggered")
        jee_obj = JeeAsk(handler_input)
        duration = get_slot_value(handler_input, "Durations")

        logger.debug(f"Duration: {duration}")

        speak_output = jee_obj.post_jee_event(isodate.parse_duration(duration).total_seconds(), RESPONSE_DURATION)

        return _handle_response(handler_input, speak_output)


class DateTimeIntentHandler(AbstractRequestHandler):
    """Handler for Date Time Intent."""

    def can_handle(self, handler_input):
        """Check for Date Time Intent."""
        return is_intent_name("Date")(handler_input)

    def handle(self, handler_input):
        """Handle the Date Time intent."""
        logger.info("Date Intent Handler triggered")
        jee_obj = JeeAsk(handler_input)

        date = get_slot_value(handler_input, "Dates")
        time = get_slot_value(handler_input, "Times")

        logger.debug(f"Dates: {date} of type {type(date)}")
        logger.debug(f"Times: {time} of type {type(time)}")

        if not date and not time:
            raise

        speak_output = jee_obj.post_jee_event(
            json.dumps({**self._parse_date(date), **self._parse_time(time)}), RESPONSE_DATE_TIME
        )

        return _handle_response(handler_input, speak_output)

    @staticmethod
    def _parse_date(date: str) -> dict:
        date_data = {
            "day": None,
            "month": None,
            "year": None,
        }

        if not date:
            return date_data

        date = date.split("-")
        date_len = len(date)

        date_data["day"] = date[2] if date_len >= 3 else None
        date_data["month"] = date[1] if date_len >= 2 else None
        date_data["year"] = date[0] if date_len >= 1 else None

        return date_data

    @staticmethod
    def _parse_time(time: str) -> dict:
        time_data = {
            "seconds": None,
            "minute": None,
            "hour": None,
        }

        if not time:
            return time_data

        # If the letter s is present then the hole time represents a second
        if "s" in time.lower():
            time_data["seconds"] = time.lower().replace("s", "")
            return time_data
        if "m" in time.lower():
            time_data["minute"] = time.lower().replace("m", "")
            return time_data
        if "h" in time.lower():
            time_data["hour"] = time.lower().replace("h", "")
            return time_data

        time = time.split(":")
        time_len = len(time)

        time_data["seconds"] = time[2] if time_len >= 3 else None
        time_data["minute"] = time[1] if time_len >= 2 else None
        time_data["hour"] = time[0] if time_len >= 1 else None

        return time_data


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Single handler for Cancel and Stop Intent."""

    def can_handle(self, handler_input):
        """Check for Cancel and Stop Intent."""
        return is_intent_name("AMAZON.CancelIntent")(handler_input) or is_intent_name("AMAZON.StopIntent")(
            handler_input
        )

    def handle(self, handler_input):
        """Handle Cancel and Stop Intent."""
        logger.info("Cancel or Stop Intent Handler triggered")
        data = handler_input.attributes_manager.request_attributes["_"]
        speak_output = data[prompts.STOP_MESSAGE]

        return _handle_response(handler_input, speak_output)

class FallbackHandler(AbstractRequestHandler):
    """Handler for Fallback."""

    def can_handle(self, handler_input):
        """Check for Select Intent."""
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        """Handle Fallback."""
        logger.info("Fallback Handler triggered")
        jee_obj = JeeAsk(handler_input)
        #reason = handler_input.request_envelope.request.reason
        #if reason == SessionEndedReason.EXCEEDED_MAX_REPROMPTS or reason == SessionEndedReason.USER_INITIATED:
        jee_obj.post_jee_event(RESPONSE_NONE, RESPONSE_NONE)

        return handler_input.response_builder.response

class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handler for Session End."""

    def can_handle(self, handler_input):
        """Check for Session End."""
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        """Clean up and stop the skill."""
        logger.info("Session Ended Request Handler triggered")
        jee_obj = JeeAsk(handler_input)
        reason = handler_input.request_envelope.request.reason
        if reason == SessionEndedReason.EXCEEDED_MAX_REPROMPTS or reason == SessionEndedReason.USER_INITIATED:
            jee_obj.post_jee_event(RESPONSE_NONE, RESPONSE_NONE)

        return handler_input.response_builder.response


class IntentReflectorHandler(AbstractRequestHandler):
    """The intent reflector is used for interaction model testing and debugging.
    It will simply repeat the intent the user said. You can create custom handlers
    for your intents by defining them above, then also adding them to the request
    handler chain below.
    """

    def can_handle(self, handler_input):
        """Check if can handle IntentReflectorHandler."""
        return is_request_type("IntentRequest")(handler_input)

    def handle(self, handler_input):
        """Simulate an intent."""
        logger.info("Reflector Intent triggered")
        intent_name = get_intent_name(handler_input)
        speak_output = "You just triggered " + intent_name + "."

        return handler_input.response_builder.speak(speak_output).response


class CatchAllExceptionHandler(AbstractExceptionHandler):
    """
    Generic error handling to capture any syntax or routing errors. If you receive an error
    stating the request handler chain is not found, you have not implemented a handler for
    the intent being invoked or included it in the skill builder below.
    """

    def can_handle(self, handler_input, exception):
        """Check if can handle exception."""
        return True

    def handle(self, handler_input, exception):
        """Handle exception."""
        logger.info("Catch All Exception triggered")
        logger.error(f"Exception :: {exception}", exc_info=True)
        jee_obj = JeeAsk()

        data = handler_input.attributes_manager.request_attributes["_"]
        if jee_obj.jee_state and jee_obj.jee_state.text:
            #speak_output = data[prompts.ERROR_ACOUSTIC].format(jee_obj.jee_state.text)
            speak_output = format(jee_obj.jee_state.text)
            logger.debug(f"Exception::speak_output 1 => {speak_output}")
            return handler_input.response_builder.speak(speak_output).ask("").set_should_end_session(True).response
            #exit()
        
        speak_output = data[prompts.ERROR_CONFIG].format(jee_obj.jee_state.text)
        logger.debug(f"Exception::speak_output 2 => {speak_output}")
        return handler_input.response_builder.speak(speak_output).set_should_end_session(True).response
        #exit()

class LocalizationInterceptor(AbstractRequestInterceptor):
    """Add function to request attributes, that can load locale specific data."""

    def process(self, handler_input):
        """Load locale specific data."""
        locale = handler_input.request_envelope.request.locale
        logger.info(f"Locale is {locale[:2]}")

        # localized strings stored in language_strings.json
        with open("language_strings.json", encoding="utf-8") as language_prompts:
            language_data = json.load(language_prompts)
        # set default translation data to broader translation
        data = language_data[locale[:2]]
        # if a more specialized translation exists, then select it instead
        # example: "fr-CA" will pick "fr" translations first, but if "fr-CA" translation exists,
        #          then pick that instead
        if locale in language_data:
            data.update(language_data[locale])
        handler_input.attributes_manager.request_attributes["_"] = data


""" 
    The SkillBuilder object acts as the entry point for your skill, routing all request and response
    payloads to the handlers above. Make sure any new handlers or interceptors you've
    defined are included below. 
    The order matters - they're processed top to bottom.
"""

sb = SkillBuilder()

# register request / intent handlers
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(YesIntentHandler())
sb.add_request_handler(NoIntentHandler())
sb.add_request_handler(StringIntentHandler())
sb.add_request_handler(SelectIntentHandler())
sb.add_request_handler(NumericIntentHandler())
sb.add_request_handler(DurationIntentHandler())
sb.add_request_handler(DateTimeIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(FallbackHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(IntentReflectorHandler())

# register exception handlers
sb.add_exception_handler(CatchAllExceptionHandler())

# register response interceptors
sb.add_global_request_interceptor(LocalizationInterceptor())

lambda_handler = sb.lambda_handler()
