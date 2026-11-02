"""Dispatch API-owned work that must outlive an HTTP request."""

def handler(event, context):
    if not isinstance(event, dict):
        raise ValueError('Invalid background task')
    task = event.get('task')
    if task == 'sync-profile':
        from profile_sync_service import run_profile_sync_event
        return run_profile_sync_event(event)
    if task == 'creator-link-click':
        from creator_click_worker import run_creator_click_event
        return run_creator_click_event(event)
    if task == 'delete-account':
        from account_deletion_service import run_event
        return run_event(event)
    if task == 'recover-account-deletions':
        from account_deletion_service import recover_event
        return recover_event(event)
    raise ValueError('Unknown background task')
