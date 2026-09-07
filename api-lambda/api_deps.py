from http import HTTPStatus

from starlette.responses import JSONResponse


def get_error_response(status_code: int, details: dict | str = None):
    status_enum = HTTPStatus(status_code)

    return JSONResponse(
        status_code=status_code,
        content={
            "code": status_code,
            "title": status_enum.phrase,
            "message": status_enum.description,
            "details": details,
        }
    )
