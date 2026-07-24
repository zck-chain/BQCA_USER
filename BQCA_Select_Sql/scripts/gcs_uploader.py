#!/usr/bin/env python3
"""
BQCA Select SQL - GCS 上传工具

将 HTML 结果上传到 GCS 公开 Bucket，返回可访问的 URL。

用法:
    from gcs_uploader import upload_html, generate_query_id
    url = await upload_html(query_id, html_content)
"""

import os
import uuid
from google.cloud import storage


def get_bucket_name() -> str:
    return os.getenv("GCS_BUCKET", "bqca-results")


def get_project() -> str:
    return os.getenv("GCP_PROJECT", "")


def generate_query_id() -> str:
    """生成唯一查询 ID，用于文件命名。"""
    return uuid.uuid4().hex[:12]


def upload_html_sync(query_id: str, html_content: str) -> str:
    """同步上传 HTML 到 GCS，返回公开 URL。"""
    client = storage.Client(project=get_project())
    bucket = client.bucket(get_bucket_name())
    blob_name = f"results/{query_id}.html"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(html_content, content_type="text/html")
    return blob.public_url


async def upload_html(query_id: str, html_content: str) -> str:
    """异步上传 HTML 到 GCS，返回公开 URL。"""
    import asyncio
    return await asyncio.to_thread(upload_html_sync, query_id, html_content)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="BQCA GCS 上传工具")
    parser.add_argument("--file", "-f", required=True, help="HTML 文件路径")
    parser.add_argument("--query-id", "-q", default=None, help="查询 ID（默认自动生成）")
    args = parser.parse_args()

    with open(args.file) as f:
        html = f.read()

    qid = args.query_id or generate_query_id()
    url = upload_html_sync(qid, html)
    print(f"Uploaded: {url}")
