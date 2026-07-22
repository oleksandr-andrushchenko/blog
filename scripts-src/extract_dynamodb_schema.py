#!/usr/bin/env python3

import json

from utils import get_dynamodb_schema

print(json.dumps(get_dynamodb_schema(), indent=2))
