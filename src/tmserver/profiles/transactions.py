"""Commit character-profile edits while retaining cross-account profile reads."""

from tmserver.transactions import transaction


def character_profile_request(host, key, payload):
    with transaction(host, (), "character_profiles") as tx:
        replies = tx.character_profiles.handle(key, payload)
        # Reads, rejected edits and identical retries do not create a storage write.
        tx.save = replies is not None and tx.character_profiles.records != host.character_profiles.records
    return replies
