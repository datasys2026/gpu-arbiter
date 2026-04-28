from __future__ import annotations


def error_payload(error_type: str, message: str, retryable: bool, **extra: object) -> dict:
    payload = {
        "error": {
            "type": error_type,
            "message": message,
            "retryable": retryable,
        }
    }
    payload["error"].update(extra)
    return payload
