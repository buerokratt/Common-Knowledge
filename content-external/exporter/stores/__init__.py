"""Object-store clients — the layer beneath the object-store sink.

base.py holds the `ExternalStoreClient` ABC (Key Design #1), its
`StoreCapabilities`, `DeleteOutcome` and the `StoreError` / `NotSupported`
exceptions. It is consumed by the object-store sink and by the manifest store,
never by the publish coordinator, which holds a `ContentSink` instead.

Still to come in Stage D: s3_store.py (D6, boto3 against whatever
EXTERNAL_S3_ENDPOINT_URL names) and factory.py (D7, the only place mapping
CONTENT_EXTERNAL_STORE_BACKEND to a class). Stage H adds azure_blob_store.py.
"""
