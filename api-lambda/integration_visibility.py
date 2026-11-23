"""Hydrate legacy verified identities when an owner opts into public display."""
from app_config import get_dynamodb_table_name
from integration_profiles import VISIBILITY_FIELDS, public_profile
from shared_utils import get_dynamodb_item


def prepare_visibility_changes(user, changes, writes):
    for provider in ("telegram", "threads", "pinterest"):
        fields = [field for field in VISIBILITY_FIELDS if field.startswith(provider + "_")]
        if not any(field in changes for field in fields):
            continue
        if not any(changes.get(field, getattr(user, field)) is True
                   for field in fields):
            continue
        key = {"pk": f"USER#{user.id}", "sk": f"INTEGRATION#{provider}"}
        connection = get_dynamodb_item(**key, consistent_read=True)
        profile = ({"title": connection.get("channel_title"), "username": connection.get("channel_username")}
                   if provider == "telegram" and connection else connection)
        changes[f"{provider}_public_profile"] = public_profile(provider, profile)
        check = {"TableName": get_dynamodb_table_name(), "Key": key,
                 "ConditionExpression": "attribute_not_exists(pk)"}
        if connection:
            identity_fields = (("channel_id", "channel_title", "channel_username", "verified_at", "bot_id")
                               if provider == "telegram" else ("session_hash", "username", "app_id", "created_at"))
            names, values, conditions = {}, {}, ["attribute_exists(pk)"]
            for index, field in enumerate(identity_fields):
                alias = f"#i{index}"
                names[alias] = field
                if field in connection:
                    value = f":i{index}"
                    values[value] = connection[field]
                    conditions.append(f"{alias} = {value}")
                else:
                    conditions.append(f"attribute_not_exists({alias})")
            check.update(ConditionExpression=" AND ".join(conditions), ExpressionAttributeNames=names)
            if values:
                check["ExpressionAttributeValues"] = values
        writes.append({"ConditionCheck": check})
