"""S3 verification and access through a server-injected storage registry."""
import time

from materials_repository import RepositoryError, _require
from domain import Problem
from materials_storage import StorageError


class CoreService:
    def __init__(self, repository, storage_registry=None, *, collector=None):
        self.repository = repository
        self.storage_registry = dict(storage_registry or {})
        self.collector = collector

    def collect(self, principal, kind, identity, storage_policy=None):
        # The read transaction is closed before the existing collector performs
        # its own catalogue/job commits. There is no second outbox or queue.
        meeting = self.repository.collection_owner(principal, kind, identity)
        _require(self.collector is not None, "collector_unconfigured", 503)
        try:
            source = self.collector(kind, meeting, storage_policy)
        except RepositoryError:
            raise
        except Problem as error:
            raise RepositoryError("collection_rejected", error.status) from None
        except (ValueError, TypeError):
            raise RepositoryError("collection_rejected", 409) from None
        _require(source is not None, "collector_disabled", 503)
        # A changed owner/room or revocation during scheduling cannot produce a
        # successful response or expose an unrelated existing source.
        with self.repository._db() as db:
            current = self.repository._one(db, "media_sources", source["id"])
            _require((current["owner_kind"], current["owner_id"]) == (kind, identity), "source_identity_conflict")
            self.repository._authorize(db, current, principal, active=True)
        return {"accepted": True, "owner_kind": kind, "owner_id": identity,
                "binding_id": current["id"], "generation": current["generation"], "state": current["state"]}

    def _storage(self, source):
        storage = self.storage_registry.get((source["provider"], source["connection_id"]))
        _require(storage is not None and storage.backend == "s3", "storage_unconfigured", 503)
        return storage

    def receive(self, principal, binding_id, receipt):
        source, prior, digest = self.repository.prepare_receipt(principal, binding_id, receipt)
        if prior is not None:
            return prior
        references = [location.model_dump() for asset in receipt.assets for location in asset.locations
                      if location.backend == "s3"]
        references.extend(asset.transcript.raw_ref.model_dump() for asset in receipt.assets if asset.transcript)
        verification = {}
        if references:
            storage = self._storage(source)
            verification["_connection_id"] = storage.connection_id
            for ref in references:
                # Only content-addressed keys in this configured store. HEAD
                # checks metadata/size, not a fresh whole-object byte hash.
                _require(ref["object_key"] == storage.object_key(ref["sha256"]), "invalid_object_key", 400)
                try:
                    if ref["object_key"] not in verification:
                        verification[ref["object_key"]] = storage.head(ref["object_key"])
                    actual = verification[ref["object_key"]]
                    _require(actual.size == ref["size"] and actual.sha256 == ref["sha256"], "object_verification_failed")
                    _require(not ref.get("content_type") or ref["content_type"] == actual.content_type,
                             "object_content_type_conflict")
                except StorageError:
                    raise RepositoryError("object_verification_failed") from None
                except RepositoryError:
                    raise
                except Exception:
                    # SDK exceptions can contain request/endpoint details.
                    raise RepositoryError("object_store_unavailable", 503) from None
        return self.repository.publish_receipt(principal, binding_id, receipt, digest, verification)

    def access(self, principal, asset_id):
        asset, source, locations = self.repository.access_context(principal, asset_id)
        owned = [r for r in locations if r["backend"] == "s3" and r["state"] == "verified"]
        if owned:
            location, storage = owned[-1], self._storage(source)
            _require(location["metadata"].get("storage_connection_id") == storage.connection_id,
                     "storage_connection_changed", 503)
            _require(location["sha256"] and location["object_key"] == storage.object_key(location["sha256"]),
                     "invalid_object_key", 409)
            try:
                result = {"id": asset_id, "storage": "owned", "url": storage.signed_url(location["object_key"], expires_seconds=300),
                          "expires": int(time.time()) + 300}
            except Exception:
                raise RepositoryError("object_store_unavailable", 503) from None
        else:
            _require(source["storage_policy"] == "remote", "owned_location_unavailable")
            _require(any(location["backend"] == "provider" and location["state"] == "available"
                         and location["object_key"] == asset["provider_asset_id"] for location in locations),
                     "remote_location_unavailable")
            result = {"id": asset_id, "storage": "remote", "binding_id": source["id"],
                      "external_id": asset["provider_asset_id"], "provider": source["provider"],
                      "connection_id": source["connection_id"], "source_ref": source["source_ref"]}
        # Authorization can change during signing; never return a capability
        # after observing revocation or a different generation.
        _, current, _ = self.repository.access_context(principal, asset_id)
        _require(current["generation"] == source["generation"], "stale_generation")
        return result
