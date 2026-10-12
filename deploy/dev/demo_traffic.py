"""Made-up gateway traffic for a VictoriaLogs store: one row per chat request,
in the shape the gateway's request log has once shipped there (the fields
LLMBench's dataset collector reads). Used by deploy/dev/demo-traffic.sh.

    python3 deploy/dev/demo_traffic.py --url http://127.0.0.1:9428 [--requests 4000]

The prompts are random text from a few templates, with shared system prompts
so a replay sees some prefix-cache reuse. Nothing measured on them means
anything about real traffic.
"""

import argparse
import json
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# (model, backend) pairs, from documentation address ranges.
BACKENDS = [
    ("qwen3-8b", "192.0.2.11:30000"),
    ("qwen3-8b", "192.0.2.12:30000"),
    ("llama-3.1-8b", "192.0.2.13:30000"),
]
SYSTEMS = [
    "You are a support assistant for an online store. Answer briefly and politely. " * 40,
    "You are a coding assistant. Explain code changes step by step and show the final code. " * 60,
    "Answer using the documents below.\n"
    + "Document: the warranty covers parts and labour for two years. " * 150,
]
QUESTIONS = [
    "How do I reset my password?",
    "Summarize the document above in three bullet points.",
    "Write a function that merges two sorted lists.",
    "Why is my order delayed?",
    "Translate the last answer into French.",
    "What does this error mean: connection refused?",
]


def rows(count: int, hours: float, seed: int):
    rnd = random.Random(seed)
    now = datetime.now(timezone.utc)  # not datetime.UTC: macOS ships Python 3.9
    for i in range(count):
        model, backend = rnd.choice(BACKENDS)
        ts = now - timedelta(seconds=rnd.uniform(60, hours * 3600))
        messages = [{"role": "system", "content": rnd.choice(SYSTEMS)}]
        for _ in range(rnd.randint(0, 3)):
            messages += [{"role": "user", "content": rnd.choice(QUESTIONS)},
                         {"role": "assistant", "content": "Sure. " * rnd.randint(20, 200)}]
        messages.append({"role": "user", "content": rnd.choice(QUESTIONS)})
        body = {"model": model, "messages": messages, "stream": True,
                "max_tokens": rnd.choice([256, 512, 1024])}
        prompt_tokens = sum(len(m["content"]) for m in messages) // 4
        yield {
            "_time": ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "_msg": f"{model} --> {backend} prompt_tokens:{prompt_tokens} status:200",
            "ts": ts.isoformat(),
            "request_id": f"demo-{seed}-{i}",
            "model": model,
            "status": "200",
            "uri": "/v1/chat/completions",
            "forwarded_to": f"http://{backend}",
            "peer": backend,
            "req_body": json.dumps(body),
            "req_body_truncated": "false",
            "resp_meta": {"model": model, "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": rnd.randint(30, 600),
                "prompt_tokens_details": {
                    "cached_tokens": int(prompt_tokens * rnd.uniform(0.5, 0.95)),
                },
            }},
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", required=True, help="the VictoriaLogs base URL")
    ap.add_argument("--requests", type=int, default=4000)
    ap.add_argument("--hours", type=float, default=24, help="spread them over this many hours")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    data = "".join(json.dumps(r) + "\n" for r in rows(args.requests, args.hours, args.seed))
    url = (args.url.rstrip("/")
           + "/insert/jsonline?_stream_fields=model&_time_field=_time&_msg_field=_msg")
    request = urllib.request.Request(url, data=data.encode(), method="POST",
                                     headers={"Content-Type": "application/stream+json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status >= 300:
            sys.exit(f"VictoriaLogs answered {response.status}")
    print(f"loaded {args.requests} requests over the last {args.hours:g} h")


if __name__ == "__main__":
    main()
