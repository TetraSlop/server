"""Commit character-profile edits while retaining cross-account profile reads."""


def character_profile_request(host, key, payload):
    profiles = host.character_profiles.stage_state()
    replies = profiles.handle(key, payload)
    if replies is not None:
        host.persist_changes({}, {"character_profiles": profiles.snapshot()})
        host.character_profiles.publish_state(profiles)
    return replies
