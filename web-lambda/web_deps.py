from http import HTTPStatus

from starlette.responses import HTMLResponse

from shared_utils import (
    get_html_content,
)


def get_error_response(status_code: int, details: dict | str = None):
    status_enum = HTTPStatus(status_code)
    public_data = {
        "code": status_code,
        "title": status_enum.phrase,
        "message": status_enum.description,
        "details": details,
    }

    content = get_html_content("error.html", {
        **public_data,
    })

    return HTMLResponse(
        status_code=status_code,
        content=content
    )
