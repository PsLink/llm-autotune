[User guide](README.md) › **Troubleshooting**

# Troubleshooting

Find the symptom, then the likely cause. Problems during the install itself
are in [Installing](../install.md#when-something-goes-wrong).

## Signing in

**The admin password is lost.** The install keeps it in
`.quickstart/secrets.env` (`AUTOTUNE_ADMIN_PASSWORD`), next to where you ran
`deploy/quickstart.sh`. `deploy/quickstart.sh ui` prints the addresses and
logins again.

## Adding a GPU cluster fails

**Connect fails.** The message says which part failed, seen from the platform's
pods (not from your machine):

- *the name did not resolve*: the kubeconfig names the API server by a name
  only your machine knows (from `/etc/hosts`, for example). Run
  `deploy/gpu-cluster.sh --api-server https://<address>:6443` to write its
  address instead.
- *the server did not answer*: the platform's pods can't reach that address.
  Check the network path, or give an address they can reach with
  `--api-server`.
- anything else: the message is the cluster's own. Running
  `deploy/gpu-cluster.sh` again writes a fresh kubeconfig; upload that one.

**No GPU nodes are listed.** The nodes don't report `nvidia.com/gpu`: the NVIDIA
device plugin isn't running on them. `kubectl describe node <node>` should show
`nvidia.com/gpu` under *Capacity*. If the GPUs use another resource name, set
**GPU resource** under **Advanced**.

## A machine

**Smoke test fails at *endpoint*.** Runs are reached on a node's address and a
NodePort. The platform's pods must be able to reach the GPU nodes' NodePorts,
and so must LLMBench's. If the nodes have several addresses, set the right one
as **Endpoint host** under **Clusters ▸ Edit**.

**Smoke test warns about *card type*.** The platform didn't recognize the GPU
model from the node's labels. Set it with **More ▸ Edit ▸ GPU type**. It
matters for comparing with a baseline, which only compares like cards.

**A machine stays *handing back*.** Its runs are finishing, as **End lease**
asked. **More ▸ End lease now** stops them at once.

## The Check step

| check | fails because | fix |
|---|---|---|
| **Container image** | the tag doesn't exist, or the nodes can't reach the registry, or it needs a login | correct the image; mirror it to a reachable registry; add **Image pull secrets** to the cluster |
| **Model path** | the folder isn't on that node | copy the weights there, or pin the campaign to machines that have them |
| **GPUs** | a candidate needs more GPUs than the machine has | remove the wide values from the search space, or add a bigger machine |
| **Policy image** | the GPU nodes can't pull the policy's image | change its image on **Policies** to a reachable registry ([Search policies](policies.md#registering-a-policy)) |
| **Engine port** (ssh) | something already listens on the port | change **First engine port** under **Machines ▸ Advanced** |

A warning (yellow) doesn't stop the campaign. "Still being pulled" means the
probe gave up waiting for a large image; the first run will wait for it
instead.

## A campaign

**It stays *scheduled*.** Its window hasn't opened yet. The schedule line says
when it will. **Run now** starts it anyway.

**It is *active*, but nothing runs.** A banner on its page says why, machine by
machine. The usual reasons:

- none of its machines is leased (**Resources ▸ Lease to platform**);
- the window is too short for another run to finish before it closes;
- the GPUs are busy with other campaigns' runs, or with something outside the
  platform;
- a pinned machine was removed.

**A policy campaign sits in *starting*.** The policy's pod hasn't started on the
GPU cluster. **Waiting on:** in the policy panel says why: usually an image the
nodes can't pull, or no node with room for it.

**A policy stays in *searching* with nothing running.** Its latest message says
what it waits for, such as "no free cards yet, will retry" while a previous
run's engine shuts down. **Logs** on the panel has the policy's own log.

**The leaderboard stays empty.** No run has succeeded yet. Look at the **Runs**
tab: every run failing points to a shared cause (below).

## Runs fail

Open the failed run on the campaign's **Runs** tab and read **Error** and
**Captured log**. [Runs and failures](runs.md#kinds-of-failure) lists every
failure kind.

**Every run fails the same way.** It is not the configs. The usual causes are a
wrong image or model path, the engine not supporting the model, or a flag that
image doesn't know. Fix it, then press **Re-run *N* failed configs**.

**`oom` on some configs.** They need more memory than the GPUs have. That is
a real result: those configs don't fit. Narrow the space (a lower
`mem_fraction_static`, fewer `max_running_requests`, a larger `tp`) if you
expected them to.

**`ready_timeout`.** The engine didn't answer within 30 minutes of starting.
Very large models can take longer to load; an admin can raise it with
`AUTOTUNE_READY_TIMEOUT_MINUTES` ([Deploying](../deploying.md#other-settings)).
Otherwise the log usually shows the engine stuck or crashed.

**`bench_preflight`.** LLMBench could not reach the engine. LLMBench's pods need
to reach the GPU nodes' NodePorts, like the platform's.

**`image_pull` or `mount_failed` on one machine only.** That node lacks the
image access or the weights. The run is retried elsewhere; pin the campaign to
the machines that work.

## On a laptop

See [Trying it all on a laptop](../install.md#trying-it-all-on-a-laptop).
The usual trap is a local proxy app: kind passes `HTTPS_PROXY=127.0.0.1:…`
into its nodes, where it points at nothing, and every image pull fails. And the
mock engine's numbers mean nothing: a ranking on a laptop is mostly noise.

---

← [Account, users and API keys](account.md) · [User guide](README.md)
