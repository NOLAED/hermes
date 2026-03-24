"""
Railway log collector — runs as a cron service every 12 hours.
Fetches runtime and HTTP logs from the Railway GraphQL API and uploads to S3.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

RAILWAY_API_URL = "https://backboard.railway.com/graphql/v2"
RAILWAY_API_TOKEN = os.getenv("RAILWAY_TOKEN")
# RAILWAY_PROJECT_ID and RAILWAY_ENVIRONMENT_ID are auto-injected by Railway.
# TARGET_SERVICE_ID must be set manually — it's the main hermes service ID,
# not the cron service's own TARGET_SERVICE_ID.
RAILWAY_PROJECT_ID = os.getenv("RAILWAY_PROJECT_ID")
RAILWAY_ENVIRONMENT_ID = os.getenv("RAILWAY_ENVIRONMENT_ID")
TARGET_SERVICE_ID = os.getenv("TARGET_SERVICE_ID")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

LOOKBACK_HOURS = 13
LOG_LIMIT = 5000


def graphql_request(query: str, variables: dict) -> dict:
    """Send a GraphQL request to the Railway API. Retries once on 429."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    headers = {
        "Content-Type": "application/json",
        "Project-Access-Token": f"{RAILWAY_API_TOKEN}",
        "User-Agent": "hermes-log-collector/1.0",
    }
    req = urllib.request.Request(RAILWAY_API_URL, data=body, headers=headers)

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                if "errors" in data:
                    print(f"GraphQL errors: {data['errors']}")
                    sys.exit(1)
                return data["data"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                print("Rate limited (429). Retrying in 60s...")
                time.sleep(60)
                continue
            print(f"Railway API error: {e.code} {e.reason}")
            try:
                print(e.read().decode())
            except Exception:
                pass
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"Railway API connection error: {e.reason}")
            sys.exit(1)

    # Should not reach here, but just in case
    print("Railway API request failed after retries")
    sys.exit(1)


def get_latest_deployment_id() -> str | None:
    """Get the latest deployment ID for the configured service/environment."""
    query = """
    query ($projectId: String!, $serviceId: String!, $environmentId: String!) {
      deployments(
        input: {
          projectId: $projectId
          serviceId: $serviceId
          environmentId: $environmentId
        }
        first: 1
      ) {
        edges {
          node {
            id
            status
          }
        }
      }
    }
    """
    variables = {
        "projectId": RAILWAY_PROJECT_ID,
        "serviceId": TARGET_SERVICE_ID,
        "environmentId": RAILWAY_ENVIRONMENT_ID,
    }
    data = graphql_request(query, variables)
    edges = data.get("deployments", {}).get("edges", [])
    if not edges:
        return None
    return edges[0]["node"]["id"]


def fetch_deployment_logs(deployment_id: str, start_time: str) -> list[dict]:
    """Fetch runtime logs (deploymentLogs) for a deployment."""
    query = """
    query ($deploymentId: String!, $startDate: String!, $limit: Int!) {
      deploymentLogs(
        deploymentId: $deploymentId
        startDate: $startDate
        limit: $limit
      ) {
        timestamp
        message
        severity
      }
    }
    """
    variables = {
        "deploymentId": deployment_id,
        "startDate": start_time,
        "limit": LOG_LIMIT,
    }
    data = graphql_request(query, variables)
    return data.get("deploymentLogs", [])


def fetch_http_logs(deployment_id: str, start_time: str) -> list[dict]:
    """Fetch HTTP logs (httpLogs) for a deployment."""
    query = """
    query ($deploymentId: String!, $startDate: String!, $limit: Int!) {
      httpLogs(
        deploymentId: $deploymentId
        startDate: $startDate
        limit: $limit
      ) {
        timestamp
        method
        path
        status
        duration
      }
    }
    """
    variables = {
        "deploymentId": deployment_id,
        "startDate": start_time,
        "limit": LOG_LIMIT,
    }
    data = graphql_request(query, variables)
    return data.get("httpLogs", [])


def format_runtime_logs(logs: list[dict]) -> str:
    """Format runtime logs as plain text: TIMESTAMP [SEVERITY] MESSAGE"""
    lines = []
    for log in logs:
        ts = log.get("timestamp", "")
        severity = log.get("severity", "INFO")
        message = log.get("message", "")
        lines.append(f"{ts} [{severity}] {message}")
    return "\n".join(lines)


def format_http_logs(logs: list[dict]) -> str:
    """Format HTTP logs as plain text: TIMESTAMP METHOD PATH STATUS DURATION"""
    lines = []
    for log in logs:
        ts = log.get("timestamp", "")
        method = log.get("method", "")
        path = log.get("path", "")
        status = log.get("status", "")
        duration = log.get("duration", "")
        lines.append(f"{ts} {method} {path} {status} {duration}")
    return "\n".join(lines)


def upload_to_s3(content: str, key: str) -> None:
    """Upload log content to S3."""
    s3_client = boto3.client("s3", region_name=AWS_REGION)
    try:
        s3_client.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=content.encode(),
            ContentType="text/plain",
        )
    except ClientError as e:
        print(f"S3 upload failed for {key}: {e}")
        sys.exit(1)


def main():
    # Validate required env vars
    required = {
        "RAILWAY_API_TOKEN": RAILWAY_API_TOKEN,
        "RAILWAY_PROJECT_ID": RAILWAY_PROJECT_ID,
        "TARGET_SERVICE_ID": TARGET_SERVICE_ID,
        "RAILWAY_ENVIRONMENT_ID": RAILWAY_ENVIRONMENT_ID,
        "S3_BUCKET_NAME": S3_BUCKET_NAME,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    start_time = (now - timedelta(hours=LOOKBACK_HOURS)).isoformat()
    timestamp_suffix = now.strftime("%H%M%S")
    date_path = now.strftime("%Y/%m/%d")

    print(f"Collecting logs from {start_time} to {now.isoformat()}")

    # Get latest deployment
    deployment_id = get_latest_deployment_id()
    if not deployment_id:
        print("Warning: No active deployment found. Nothing to collect.")
        sys.exit(0)
    print(f"Latest deployment: {deployment_id}")

    # Fetch logs
    runtime_logs = fetch_deployment_logs(deployment_id, start_time)
    http_logs = fetch_http_logs(deployment_id, start_time)
    print(f"Fetched {len(runtime_logs)} runtime logs, {len(http_logs)} HTTP logs")

    # Format and upload runtime logs
    if runtime_logs:
        runtime_text = format_runtime_logs(runtime_logs)
        runtime_key = f"logs/railway/runtime/{date_path}/runtime-{timestamp_suffix}.log"
        upload_to_s3(runtime_text, runtime_key)
        print(f"Uploaded runtime logs to s3://{S3_BUCKET_NAME}/{runtime_key}")
    else:
        print("No runtime logs to upload")

    # Format and upload HTTP logs
    if http_logs:
        http_text = format_http_logs(http_logs)
        http_key = f"logs/railway/http/{date_path}/http-{timestamp_suffix}.log"
        upload_to_s3(http_text, http_key)
        print(f"Uploaded HTTP logs to s3://{S3_BUCKET_NAME}/{http_key}")
    else:
        print("No HTTP logs to upload")

    print("Log collection complete.")


if __name__ == "__main__":
    main()
