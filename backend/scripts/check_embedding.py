"""从任意工作目录检查项目在线意图向量；本地降级或关闭时以非零状态退出。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / '.env')

from core.intent_recognizer import IntentRecognizer


async def check(message):
    recognizer = IntentRecognizer(api_key=os.getenv('ANTHROPIC_API_KEY') or 'embedding-check-only')
    try:
        return await recognizer.check_embedding(message)
    finally:
        await recognizer.client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message', default='这笔钱能退给我吗？')
    args = parser.parse_args()
    if not args.message.strip() or len(args.message) > 2000:
        parser.error('message 必须为 1 到 2000 个字符的非空文本')
    result = asyncio.run(check(args.message))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
