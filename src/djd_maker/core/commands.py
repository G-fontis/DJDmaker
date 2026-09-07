"""GUI-independent application command contract, shared by every presentation."""
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Callable


@unique
class CommandId(StrEnum):
    SETTINGS_OPEN = 'CMD_SETTINGS_OPEN'
    GOOGLE_LOGIN = 'CMD_GOOGLE_LOGIN'
    CLASS_VIDEO_START = 'CMD_CLASS_VIDEO_START'
    SCRIPT_RELOAD = 'CMD_SCRIPT_RELOAD'
    RECOVERY_START = 'CMD_RECOVERY_START'
    PAUSE = 'CMD_PAUSE'
    RESUME = 'CMD_RESUME'
    STOP = 'CMD_STOP'
    LOG_OPEN = 'CMD_LOG_OPEN'
    JOB_DETAIL_OPEN = 'CMD_JOB_DETAIL_OPEN'
    DELETE_SELECTED = 'CMD_DELETE_SELECTED'
    DELETE_COMPLETED = 'CMD_DELETE_COMPLETED'
    GUI_SWITCH_PHASE1 = 'CMD_GUI_SWITCH_PHASE1'
    GUI_SWITCH_PHASE2 = 'CMD_GUI_SWITCH_PHASE2'
    INPUT_OPEN = 'CMD_INPUT_OPEN'
    RAW_OPEN = 'CMD_RAW_OPEN'
    OUTPUT_OPEN = 'CMD_OUTPUT_OPEN'
    ENDING_CHANGE = 'CMD_ENDING_CHANGE'
    ENDING_PREVIEW = 'CMD_ENDING_PREVIEW'


REQUIRED_COMMANDS = frozenset(CommandId)
BUTTON_COMMANDS = {
    'settings_button': CommandId.SETTINGS_OPEN,
    'login_button': CommandId.GOOGLE_LOGIN,
    'start_button': CommandId.CLASS_VIDEO_START,
    'reload_button': CommandId.SCRIPT_RELOAD,
    'recover_button': CommandId.RECOVERY_START,
    'pause_button': CommandId.PAUSE,
    'resume_button': CommandId.RESUME,
    'stop_button': CommandId.STOP,
    'log_button': CommandId.LOG_OPEN,
    'details_button': CommandId.JOB_DETAIL_OPEN,
    'delete_selected_button': CommandId.DELETE_SELECTED,
    'delete_completed_button': CommandId.DELETE_COMPLETED,
    'open_input_button': CommandId.INPUT_OPEN,
    'open_raw_folder_button': CommandId.RAW_OPEN,
    'open_output_button': CommandId.OUTPUT_OPEN,
    'change_ending_button': CommandId.ENDING_CHANGE,
    'preview_ending_button': CommandId.ENDING_PREVIEW,
    'completion_output_button': CommandId.OUTPUT_OPEN,
    'completion_raw_button': CommandId.RAW_OPEN,
    'completion_error_button': CommandId.LOG_OPEN,
}


class InterfaceError(ValueError):
    pass


def validate_payload(command, payload):
    if not isinstance(payload, dict):
        raise InterfaceError('INTERFACE_ERROR: payload must be an object')
    if command in {CommandId.DELETE_SELECTED, CommandId.DELETE_COMPLETED}:
        if set(payload) != {'job_ids'} or not isinstance(payload['job_ids'], list) or any(not isinstance(i, str) or not i for i in payload['job_ids']):
            raise InterfaceError('INTERFACE_ERROR: job_ids must be a list of IDs')
    elif command in {CommandId.GUI_SWITCH_PHASE1, CommandId.GUI_SWITCH_PHASE2}:
        expected = 'PHASE1' if command == CommandId.GUI_SWITCH_PHASE1 else 'PHASE2'
        if payload != {'gui_type': expected}:
            raise InterfaceError('INTERFACE_ERROR: GUI type/command mismatch')
    elif payload:
        raise InterfaceError('INTERFACE_ERROR: unexpected payload')


class CommandRouter:
    def __init__(self, handlers):
        self.handlers = {}
        for command, handler in handlers:
            if not isinstance(command, str):
                raise InterfaceError('INTERFACE_ERROR: invalid command registration')
            if command in self.handlers:
                raise InterfaceError('INTERFACE_ERROR: duplicate command ID')
            if command not in REQUIRED_COMMANDS or not callable(handler):
                raise InterfaceError('INTERFACE_ERROR: invalid command registration')
            self.handlers[command] = handler
        if set(self.handlers) != REQUIRED_COMMANDS:
            raise InterfaceError('INTERFACE_ERROR: incomplete command registry')

    def dispatch(self, command, payload=None):
        if not isinstance(command, str) or command not in self.handlers:
            raise InterfaceError('INTERFACE_ERROR: UNKNOWN_COMMAND_ID')
        payload = {} if payload is None else payload
        validate_payload(command, payload)
        return self.handlers[command](payload)


@unique
class EventId(StrEnum):
    JOB_UPDATED = 'EVT_JOB_UPDATED'
    JOBS_UPDATED = 'EVT_JOBS_UPDATED'
    RUNTIME_STATUS = 'EVT_RUNTIME_STATUS'
    PHASE_CHANGED = 'EVT_PHASE_CHANGED'
    LIMIT_DETECTED = 'EVT_LIMIT_DETECTED'
    LIMIT_WAITING = 'EVT_LIMIT_WAITING'
    LIMIT_RELEASED = 'EVT_LIMIT_RELEASED'
    PAUSED = 'EVT_PAUSED'
    STOPPED = 'EVT_STOPPED'
    ERROR = 'EVT_ERROR'


@dataclass(frozen=True)
class PresentationEvent:
    id: EventId
    payload: object
