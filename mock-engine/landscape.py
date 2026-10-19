"""An opt-in performance landscape for the mock engine: real sglang flags move
the numbers, the way they would on a GPU — roughly, and on purpose simply.

Off by default; `--mock-landscape sglang` turns it on. A search policy can then
be exercised end to end without a GPU and still have something to find:
capacity limits, interactions, an out-of-memory edge, a trade-off that flips
with concurrency, and a little run-to-run noise. The numbers mean nothing about
any model; the shape is what makes a search policy's choices testable.

The model, per engine process:
  * KV capacity (tokens) grows with `mem_fraction_static` (and doubles with an
    fp8 KV cache). A request holds prompt+output tokens of it while it runs;
    when it is full, new requests wait. `max_running_requests` caps the batch.
  * Prefill runs one request at a time, faster with a larger
    `chunked_prefill_size` (up to 16k); a larger chunk slows decode a little
    while prefills interleave (less so with `enable_mixed_chunk`).
  * Each decode step slows as the batch grows; past `cuda_graph_max_bs` it
    runs without CUDA graphs and is much slower. `attention_backend`,
    `enable_torch_compile` (small batches only) and `num_continuous_decode_steps`
    shift it; speculative decoding (NEXTN) speeds small batches, slows big ones.
  * Too much memory fails at startup with the OOM a real engine prints, as does
    a speculative algorithm that needs a draft model it was not given.
"""

from __future__ import annotations

import random
import threading
import time

DEFAULTS = {
    "mem_fraction_static": 0.85,
    "max_running_requests": None,
    "chunked_prefill_size": 8192,
    "cuda_graph_max_bs": 160,
    "schedule_policy": "fcfs",
    "attention_backend": "flashinfer",
    "enable_torch_compile": False,
    "torch_compile_max_bs": 32,
    "enable_mixed_chunk": False,
    "num_continuous_decode_steps": 1,
    "kv_cache_dtype": "auto",
    "speculative_algorithm": None,
    "speculative_draft_model_path": None,
    "speculative_num_steps": 3,
    "disable_radix_cache": False,
}
ATTENTION = {"fa3": 0.93, "flashinfer": 1.0, "triton": 1.12, "torch_native": 2.0}
SCHEDULE = {"fcfs": 1.0, "lpm": 1.01, "dfs-weight": 1.02, "lof": 1.0, "random": 1.04}


def _value(raw):
    if isinstance(raw, bool) or raw is None:
        return raw
    text = str(raw)
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def parse_flags(unknown: list[str]) -> dict:
    """`--flag value` / `--flag` pairs, as snake_case keys with typed values."""
    out: dict = {}
    i = 0
    while i < len(unknown):
        token = unknown[i]
        if not token.startswith("--"):
            i += 1
            continue
        key, _, inline = token[2:].partition("=")
        key = key.replace("-", "_")
        if inline:
            out[key] = _value(inline)
            i += 1
        elif i + 1 < len(unknown) and not unknown[i + 1].startswith("--"):
            out[key] = _value(unknown[i + 1])
            i += 2
        else:
            out[key[3:] if key.startswith("no_") else key] = not key.startswith("no_")
            i += 1
    return out


class StartupError(Exception):
    """A failure the engine would print before it serves (OOM, a bad argument)."""


class Landscape:
    def __init__(self, flags: dict, base_token_ms: float, seed: int | None = None):
        args = {**DEFAULTS, **{k: v for k, v in flags.items() if k in DEFAULTS or k in ("tp", "tp_size")}}
        self.args = args
        mem = float(args["mem_fraction_static"])
        graph_bs = int(args["cuda_graph_max_bs"])
        chunk = int(args["chunked_prefill_size"])
        if mem > 0.93:
            raise StartupError(
                "Capturing CUDA graphs... torch.OutOfMemoryError: CUDA out of memory. Tried to allocate "
                "2.00 GiB. GPU 0 has a total capacity of 79.10 GiB of which 1.12 GiB is free."
            )
        if graph_bs > 384 and mem > 0.90:
            raise StartupError(
                f"Capturing CUDA graphs for batch sizes up to {graph_bs}... torch.OutOfMemoryError: "
                "CUDA out of memory while capturing cuda graph."
            )
        if chunk > 32768 and mem > 0.90:
            raise StartupError(
                f"Prefill warmup with chunked_prefill_size={chunk}: torch.OutOfMemoryError: CUDA out of memory."
            )
        algo = args["speculative_algorithm"]
        if algo and str(algo).upper() not in ("NEXTN", "MTP") and not args["speculative_draft_model_path"]:
            raise StartupError(f"ValueError: speculative_algorithm={algo} requires --speculative-draft-model-path")
        self.rng = random.Random(seed)
        self.noise = self.rng.gauss(1.0, 0.015)
        kv_factor = 2.0 if str(args["kv_cache_dtype"]).startswith("fp8") else 1.0
        self.kv_capacity = int(max(0.0, mem - 0.6) * 600_000 * kv_factor)
        cap = args["max_running_requests"]
        self.max_running = int(cap) if cap else 10_000
        self.chunk = chunk
        self.graph_bs = graph_bs
        self.base_token_ms = base_token_ms
        self.prefill_rate = 40_000 * (min(chunk, 16384) / 8192) ** 0.4  # tokens/s
        self.startup_extra = 20.0 if args["enable_torch_compile"] else 0.0
        # live state
        self.lock = threading.Condition()
        self.kv_used = 0
        self.running = 0
        self.waiting = 0
        self.prefilling = 0
        self.prefill_lock = threading.Lock()
        self.generated = 0

    # -- the request's life ---------------------------------------------------------
    def admit(self, tokens: int) -> None:
        """Wait for a batch slot and KV room for `tokens`."""
        need = min(tokens, self.kv_capacity)
        with self.lock:
            self.waiting += 1
            while self.running >= self.max_running or self.kv_used + need > self.kv_capacity:
                self.lock.wait(0.05)
            self.waiting -= 1
            self.running += 1
            self.kv_used += need

    def release(self, tokens: int) -> None:
        with self.lock:
            self.running -= 1
            self.kv_used -= min(tokens, self.kv_capacity)
            self.lock.notify_all()

    def prefill(self, prompt_tokens: int) -> None:
        """Prefills run one at a time, as a scheduler serializes them."""
        with self.prefill_lock:
            self.prefilling += 1
            factor = 1.1 if int(self.args["num_continuous_decode_steps"]) > 1 else 1.0
            time.sleep(prompt_tokens / self.prefill_rate * factor)
            self.prefilling -= 1

    def step_ms(self) -> float:
        """Time between two tokens of one request, given the batch right now."""
        a = max(1, self.running)
        t = self.base_token_ms * self.noise * (1 + 0.012 * a)
        if a > self.graph_bs:
            t *= 1.35
        t *= ATTENTION.get(str(self.args["attention_backend"]), 1.05)
        t *= SCHEDULE.get(str(self.args["schedule_policy"]), 1.0)
        if self.args["enable_torch_compile"] and a <= int(self.args["torch_compile_max_bs"]):
            t *= 0.94
        if int(self.args["num_continuous_decode_steps"]) > 1:
            t *= 0.97
        if self.args["disable_radix_cache"]:
            t *= 0.995
        if self.args["speculative_algorithm"]:
            steps = int(self.args["speculative_num_steps"])
            t *= (0.62 + 0.03 * steps) if a < 16 else (1.08 + 0.02 * steps)
        interference = 0.08 * (self.chunk / 8192) * (1.0 if self.prefilling else 0.0)
        if self.args["enable_mixed_chunk"]:
            interference *= 0.6
        return t * (1 + interference)

    def token_out(self) -> None:
        with self.lock:
            self.generated += 1

    # -- what an engine prints --------------------------------------------------------
    def server_args_line(self) -> str:
        shown = ", ".join(f"{k}={self.args[k]!r}" for k in sorted(self.args))
        return f"server_args=ServerArgs({shown}, max_total_num_tokens={self.kv_capacity})"

    def start_logger(self, interval: float = 10.0) -> None:
        def run():
            last, last_t = 0, time.time()
            while True:
                time.sleep(interval)
                with self.lock:
                    generated, running, waiting, used = self.generated, self.running, self.waiting, self.kv_used
                now = time.time()
                rate = (generated - last) / max(1e-6, now - last_t)
                last, last_t = generated, now
                if running or waiting:
                    print(
                        f"Decode batch. #running-req: {running}, #queue-req: {waiting}, "
                        f"#token: {used}, token usage: {used / max(1, self.kv_capacity):.2f}, "
                        f"cuda graph: {running <= self.graph_bs}, gen throughput (token/s): {rate:.2f}",
                        flush=True,
                    )
        threading.Thread(target=run, daemon=True).start()
