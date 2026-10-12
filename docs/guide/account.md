[User guide](README.md) › **Account, users and API keys**

# Account, users and API keys

The user menu (top right) has **Account** and **API keys**; the language
switch next to it changes the interface between English and Chinese.

![The user menu](images/user-menu.png)

## Users and roles

The install creates one user, `admin`, with the printed password. Other
accounts are created in one of two ways:

- **Create account** on the sign-in page, for anyone who can reach the UI. New
  accounts are ordinary users. An install can turn this off
  (`allowSignup: false`, [After installing](../after-installing.md#users)).
- **Add user** on the **Account** page, by an admin: a username, an initial
  password and a role.

Roles:

- **Admins** can also add and edit GPU clusters, register their nodes, see
  and revoke every user's API keys, and manage users. These controls are
  hidden from other users.
- **Users** can do everything else: lease machines, create search spaces and
  objectives, create and run campaigns, and create their own API keys.

The **Users** list at the bottom of the **Account** page (admins only) adds
users and changes roles. The last admin cannot be demoted.

## Your account

![The Account page](images/account.png)

- **Profile**: username, role, and when the account was created.
- **Change password**: at least 6 characters. The current session stays signed
  in, and API keys keep working.
- **Published name**: the name benchmark submissions appear under on
  LLMBench. Defaults to the username.

## API keys

Scripts and other systems call the platform's API with an **API key** instead
of a password. A key acts with the permissions of the user who created it and
appears in the audit records as `key:<name>`.

Open **API keys** from the user menu and press **Mint a key**. Name it after
the system that will use it, such as `fleet-manager`.

![A newly created key](images/api-key-minted.png)

The key is shown **once**; copy it then. Only a hash is stored, so a lost key
is replaced, not recovered. The dialog includes an example call to this
platform's address.

Send the key in the `X-API-Key` header:

```bash
curl http://localhost:8080/api/campaigns -H "X-API-Key: $KEY"
```

The list shows your keys: name, prefix, and when each was last used. Admins
see every user's keys, with an **Owner** column. **Revoke** disables a key
immediately. Keys the platform creates for a running policy are not listed;
they belong to that policy session and expire with it.

![The API keys list](images/api-keys.png)

**API docs** opens the interactive reference of every endpoint. The APIs for
other systems are described in:

- [Machine lease API](../api/machine-lease.md): leasing machines to the
  platform from a scheduler, and ending the lease;
- [Policy API](../api/policy-api.md): what a search policy calls;
- [Agent API](../api/agent-api.md): the read-only data used for LLM-written
  performance reports (off by default).

---

← [Runs and failures](runs.md) · [User guide](README.md) · Next: [Troubleshooting](troubleshooting.md) →
