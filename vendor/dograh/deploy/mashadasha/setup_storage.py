"""Initialize the new private bucket; never enable anonymous storage access."""
import os
import boto3
from botocore.exceptions import ClientError

client = boto3.client("s3", endpoint_url="http://minio:9000")
bucket = os.environ["S3_BUCKET"]
try:
    client.head_bucket(Bucket=bucket)
except ClientError as exc:
    if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
        raise
    client.create_bucket(Bucket=bucket)
try:
    client.get_bucket_policy(Bucket=bucket)
except ClientError as exc:
    if exc.response["Error"]["Code"] != "NoSuchBucketPolicy":
        raise
else:
    raise RuntimeError("Unexpected existing bucket policy; refusing to alter it")

key = "verification/private-storage-check.txt"
client.put_object(Bucket=bucket, Key=key, Body=b"Synthetic deployment verification.")
public_client = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT_URL"],
    config=boto3.session.Config(signature_version="s3v4", s3={"addressing_style": "path"}))
url = public_client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=60)
from urllib.request import urlopen
from urllib.error import HTTPError
with urlopen(url, timeout=15) as response:
    assert response.read() == b"Synthetic deployment verification."
try:
    urlopen(os.environ["S3_ENDPOINT_URL"] + "/" + bucket + "/" + key, timeout=15)
except HTTPError as exc:
    assert exc.code == 403
else:
    raise RuntimeError("Anonymous storage read unexpectedly succeeded")
print("Private bucket ready: signed GET succeeded, unsigned GET denied (403)")
