import importlib.util
import os
import sys


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


web_module = load_module("local_web_app", os.path.join(os.getenv("WEB_LAMBDA_DIR", "/app/web-lambda"), "app.py"))
api_module = load_module("local_api_app", os.path.join(os.getenv("API_LAMBDA_DIR", "/app/api-lambda"), "app.py"))
web_module.app.router.routes = api_module.app.router.routes + web_module.app.router.routes
# The combined test app needs API compatibility/headers that production web
# and API Lambdas keep separate.
web_module.app.middleware("http")(api_module.redirect_legacy_api_endpoints)
web_module.app.middleware("http")(api_module.add_no_robots_to_api)
app = web_module.app
