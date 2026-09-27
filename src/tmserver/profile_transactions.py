"""Commit character-profile edits while retaining cross-account profile reads."""


def character_profile_request(host, key, payload):
    profiles = host.character_profiles.stage_state()
    replies = profiles.handle(key, payload)
    # Reads, rejected edits and identical retries do not create a storage write.
    if replies is not None and profiles.records != host.character_profiles.records:
        host.persist_changes({}, {"character_profiles": profiles.snapshot()})
        host.character_profiles.publish_state(profiles)
    return replies
