#!/usr/bin/env python3
import yaml
import json

CF_FILE = "cf.yml"
RESOURCE_NAME = "DynamoDBTable"


# Ignore unknown CF tags like !Sub, !Ref
def ignore_unknown(loader, tag_suffix, node):
    return loader.construct_scalar(node)


yaml.add_multi_constructor("!", ignore_unknown)

with open(CF_FILE, "r") as f:
    cf = yaml.load(f, Loader=yaml.FullLoader)

props = cf.get("Resources", {}).get(RESOURCE_NAME, {}).get("Properties", {})

# Keep only DynamoDB keys
allowed_keys = {"TableName", "BillingMode", "AttributeDefinitions", "KeySchema", "GlobalSecondaryIndexes"}
schema = {k: v for k, v in props.items() if k in allowed_keys}

print(json.dumps(schema, indent=2))
