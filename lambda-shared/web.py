import inspect
from dataclasses import asdict, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Annotated, get_args, get_origin, get_type_hints

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route


class RequestValidationError(ValueError):
    def __init__(self, details):
        self.details = details
        super().__init__(str(details))

    def errors(self):
        return [
            {"loc": ("request", field), "msg": message}
            for field, message in self.details.items()
        ]


def parse_dto(dto_type, data):
    try:
        allowed = {field.name for field in fields(dto_type) if field.init}
        return dto_type(**{k: v for k, v in data.items() if k in allowed})
    except (TypeError, ValueError) as exc:
        message = str(exc)
        field = message.split(" ", 1)[0] if message else "request"
        raise RequestValidationError({field: message}) from exc


def jsonable(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if is_dataclass(value):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    return value


def json_response(value, status_code=200):
    return JSONResponse(jsonable(value), status_code=status_code)


class Depends:
    def __init__(self, dependency=None): self.dependency = dependency


class Query:
    def __init__(self, default=None): self.default = default


class Body(Query):
    pass


async def resolve(function, request):
    hints = get_type_hints(function, include_extras=True)
    values = {}
    body = None
    for name, parameter in inspect.signature(function).parameters.items():
        annotation = hints.get(name, parameter.annotation)
        metadata = get_args(annotation)[1:] if get_origin(annotation) is Annotated else ()
        dependency = next((item for item in metadata if isinstance(item, Depends)), None)
        if dependency:
            values[name] = await resolve(dependency.dependency or get_args(annotation)[0], request)
        elif annotation is Request:
            values[name] = request
        elif name in request.path_params:
            values[name] = request.path_params[name]
        elif inspect.isclass(annotation) and is_dataclass(annotation):
            body = await request.json() if body is None else body
            values[name] = parse_dto(annotation, body)
        else:
            default = parameter.default.default if isinstance(parameter.default, Query) else parameter.default
            if default is inspect.Parameter.empty: default = None
            values[name] = request.query_params.getlist(name) if get_origin(
                annotation) is list else request.query_params.get(name, default)
    if inspect.isclass(function) and is_dataclass(function):
        return parse_dto(function, values)
    result = function(**values)
    return await result if inspect.isawaitable(result) else result


class Application(Starlette):
    def __init__(self, **kwargs):
        super().__init__()
        self.url_routes = []

    def add_url_route(self, path, name):
        """Register route metadata for URL generation without a request handler."""
        self.url_routes.append(Route(path, lambda request: Response(status_code=404), name=name))

    def middleware(self, middleware_type):
        def register(function):
            self.add_middleware(BaseHTTPMiddleware, dispatch=function)
            return function

        return register

    def exception_handler(self, exception_type):
        def register(function):
            self.exception_handlers[exception_type] = function
            return function

        return register

    def _route(self, path, methods, name, response_class, status_code):
        def register(endpoint):
            async def dispatch(request):
                result = await resolve(endpoint, request)
                if isinstance(result, Response): return result
                if status_code == 204: return Response(status_code=204)
                if response_class is HTMLResponse: return HTMLResponse(result, status_code=status_code)
                return json_response(result, status_code)

            self.router.routes.append(Route(path, dispatch, methods=methods, name=name))
            return endpoint

        return register

    def get(self, path, *, name=None, response_class=None, status_code=200):
        return self._route(path, ["GET"], name, response_class, status_code)

    def post(self, path, *, name=None, response_class=None, status_code=200):
        return self._route(path, ["POST"], name, response_class, status_code)

    def patch(self, path, *, name=None, response_class=None, status_code=200):
        return self._route(path, ["PATCH"], name, response_class, status_code)

    def delete(self, path, *, name=None, response_class=None, status_code=200):
        return self._route(path, ["DELETE"], name, response_class, status_code)
