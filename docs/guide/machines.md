[User guide](README.md) › **Machines and GPU clusters**

# Machines and GPU clusters

Everything about where runs happen is on the **Resources** page. A **machine**
is one place a run can land. There are two kinds:

- **A GPU node in a Kubernetes cluster.** You add the cluster once, and each
  GPU node you register becomes a machine. This is the usual setup.
- **A bare-metal box reached over ssh.** The platform logs in and starts the
  engine with `docker run`.

A machine is only used while it is **leased** to the platform.

## Adding a GPU cluster

*Admins only:* other users don't see **Add GPU cluster**. First, with that
cluster's admin kubeconfig, run
`deploy/gpu-cluster.sh --context <gpu-cluster>`
([Installing, step 2](../install.md#2-prepare-a-gpu-cluster)). It creates a
namespace for the runs (`llm-autotune-runs`) and an account that may manage
only the platform's own workloads there and read the nodes, and writes
`llm-autotune-runs.kubeconfig`, which holds only that account's token.

Then press **Add GPU cluster**, upload the file, name the cluster and press
**Connect**.

![Add GPU cluster](images/add-cluster-connect.png)

The kubeconfig is stored encrypted and never shown again. **Advanced** has the
settings most clusters don't need:

| setting | when to change it |
|---|---|
| **Namespace** | The namespace runs go into. Read from the kubeconfig. |
| **Workload kind** | `deployment` (the default) creates each run as a Deployment. `custom` hands runs to the optional TuningRun operator ([operator/INSTALL.md](../../operator/INSTALL.md)). |
| **RuntimeClass** | The container runtime class engine pods ask for, `nvidia` by default. |
| **GPU resource** | The resource name GPUs are requested by, `nvidia.com/gpu` by default. |
| **Extra tolerations** | For GPU nodes with taints besides the usual GPU taint, which is tolerated on its own. Comma-separated `key[=value][:effect]`. |
| **Image pull secrets** | For engine images in a registry that needs a login: the names of secrets that already exist in the runs' namespace. |

After **Connect** the platform lists the cluster's GPU nodes with their cards.
Tick the ones to use (new ones are ticked already) and press **Register**.

![Registering a cluster's GPU nodes](images/add-cluster-nodes.png)

### The Clusters dialog

**Clusters** lists the clusters you added.

![The Clusters dialog](images/clusters-dialog.png)

- **Nodes** registers nodes added to the cluster since, and lists registered
  machines whose node has gone.
- **Probe** checks that the credential still works and refreshes what the
  platform knows about the cluster.
- **Edit** changes its settings, including a few only found here:
  - the **Endpoint host**, the address runs are reached on (by default a
    node's own address);
  - the size of `/dev/shm` in engine pods;
  - a **Default node selector**;
  - CPU and memory requests and limits for engine pods.

  Leave **Kubeconfig** empty to keep the stored one, or paste a new one to
  replace it.
- **Remove** deletes the cluster and its stored kubeconfig from the platform.
  Machines on it must be removed (or moved to another cluster) first. Nothing
  on the cluster itself changes; `deploy/gpu-cluster.sh remove` revokes the
  account there.

## The machine card

![A leased machine](images/machine-leased.png)

From left to right:

- **The name.** For a cluster node it is the node's name.
- **Tags:** `k8s` and the cluster's name for a cluster node. The lease state:
  *not leased*, *leased*, *running* (runs in progress) or *handing back*. While
  it is leased, also what an outside system asking for the machine back would
  be told: *idle*, *busy* or *returnable*.
- **Cards in use**, out of the machine's total, and the GPU type when the
  platform recognizes it.

Below that, a track shows where the machine is in its lease: **Leased**, then
**Running campaigns**, then **Handed back**. A sentence under the track says
what is happening now, and its **?** says what would happen if you ended the
lease. While campaigns use the machine, links to them appear below.

## Leasing

**Lease to platform** lets campaigns use the machine. Runs start on it only
while it is leased, and only inside a campaign's window.

To give it back:

- **End lease** is the polite way. Runs already going finish first; nothing new
  starts (the machine shows *handing back*). Then it is released. The dialog
  says how long that may take.
- **More ▸ End lease now (stops runs)** stops the platform's runs on it at once.
  Their measurements are lost.

Either way, only the platform's own runs stop. Nothing else on the machine is
touched.

Leases can also come from another system, such as a scheduler that lends idle
GPUs at night, through the [machine lease API](../api/machine-lease.md).
**API** on this page opens the API reference.

## Smoke test

**Smoke test** checks that the platform can reach the machine and is allowed
to do what a run needs. On a cluster node it checks the credential, the
permissions, that the node is ready with free GPUs, and that runs on it can be
reached. Tick **Probe pod** and press **Run again** to also place a small pod
on the node, asking for no GPUs.

![Smoke test results](images/smoke-test.png)

A *warn* line is worth reading but doesn't stop a run. A *fail* line names
what to fix.

## More ▸

- **Edit** changes the machine's name, card count, GPU type and notes.
- **Remove** deletes it from the platform. On a cluster, **Clusters ▸ Nodes**
  can register the node again.
- **Re-read GPUs** asks the machine again how many cards of which type it has.

Set the **GPU type** (such as `H100`) when the platform cannot read it. Results
are only compared with a baseline measured on the same type of card.

## Bare-metal machines over ssh

The worker logs in over ssh and runs the engine with `docker run`.

1. Give the worker an ssh key at install time (`worker.ssh` in your values;
   see [After installing](../after-installing.md#bare-metal-machines-over-ssh)).
2. On the machine, put the key's public half in the ssh user's
   `~/.ssh/authorized_keys`. The machine needs Docker and the NVIDIA container
   toolkit.
3. Press **Add machine**, keep **Driver** at *SSH + Docker*, and enter a
   **Name** and the **Host** address (both required). Under **Advanced**: the ssh user and
   port, the GPU count and type if the machine cannot report them, and the
   notes.
4. **Smoke test** it, then **Lease to platform**.

**Add machine** with the *Kubernetes* driver is for a special case: a slice of
a cluster you already added, limited by a **Node selector** (`label=value`),
for a model whose weights are on only some nodes. **Add GPU cluster** already
registers each GPU node, so most people never need this.

## Node groups (multi-node)

A model too large for one node can be served across several. **Group
machines** joins leased machines into a named group; the first one you pick is
the master. Members must share a driver, a GPU type and a card count.

A group is not a reservation. Each member stays an ordinary machine that other
campaigns can use, and is taken only while a multi-node run is deployed on it.
**Preflight** on a group checks that every member is ready and can reach the
master. A campaign picks the group on its **Machines** step (sglang only).
Most setups never need one.

---

← [Your first campaign](first-campaign.md) · [User guide](README.md) · Next: [Search spaces](search-spaces.md) →
