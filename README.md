# codex-container

Run the [OpenAI Codex CLI](https://github.com/openai/codex) inside a disposable
container with profiles. Your current directory is mounted as `/workspace`,
your host Git identity is forwarded in, and Codex runs with
`--dangerously-bypass-approvals-and-sandbox`. It can modify files in the mounted
workspace and persistent state. Enabled hooks and launch folders are trusted
automatically, without startup trust prompts.

Works with **Podman** or **Docker Engine** on Linux. The wrapper prefers Podman
when both are installed; set `CONTAINER_ENGINE=docker` to select Docker explicitly.
Use a local engine: bind-mounted paths and user IDs must refer to this host.

This is a convenience wrapper, not a hardened security boundary. Codex has
access to the mounted workspace, profile state, shared credentials, any
forwarded GitHub token, and the network. Tools installed in the container
disappear when it exits; workspace changes and Codex state persist on the host.

## Quickstart

```bash
git clone https://github.com/mortrevere/codex-container.git
cd codex-container
./codex-container login --device-auth
./codex-container
```

Open the login URL on your host and enter the device code. Device login may
need enabling in your ChatGPT security settings. `./codex-container login`
also supports browser login; open its printed URL on the host. Browser callback
reachability depends on your container engine's host-network behavior.

The first invocation builds the image automatically. Docker must be running
and accessible to your user (`docker info` must succeed).

## Installation

Keep `codex-container`, `codex-container-init`, `Dockerfile`, and `profiles/`
together. For example:

```bash
mkdir -p ~/.local/lib/codex-container
cp -a codex-container codex-container-init Dockerfile profiles \
  ~/.local/lib/codex-container/
```

Add this to `~/.bashrc` or `~/.zshrc`:

```bash
alias codex='~/.local/lib/codex-container/codex-container'
```

Alternatively, point the alias directly at this checkout's `codex-container`.
Installing only the wrapper is insufficient: it also needs the initialization
script and profile files next to it.

To build manually:

```bash
podman build . -f Dockerfile -t codex-container
# Or:
docker build . -f Dockerfile -t codex-container
```

The image installs the official standalone Codex CLI, `gh`, `uv`, and `ruff`,
plus Node.js for plugin hooks such as Ponytail's.

## Usage

```bash
codex                              # interactive session in $PWD
codex "explain this repository"     # interactive session with an initial prompt
codex exec "fix the failing test"   # non-interactive task
codex resume                       # select a saved session in this profile
codex resume --all                  # include sessions from other working directories
codex resume --last                 # resume the most recent session
codex --profile pony               # start with the Ponytail plugin
codex --profile mcp                # start with Kubernetes and Grafana MCP servers
codex --profile pr create "focus on the API changes"
codex --profile pr describe "https://github.com/OWNER/REPO/pull/123"
codex --profile pr review "https://github.com/OWNER/REPO/pull/123"
codex --profile pr resume <session-id>
codex --profile pr --model <model-id> create "focus on the API changes"
CODEX_PROFILE=pony codex            # select a profile through the environment
codex login --device-auth           # log in for all profiles
codex login status                 # check the shared account
codex logout                       # sign out the shared account
codex bash                         # debug inside the container
codex update                       # rebuild without cache, keeping a backup image tag
CONTAINER_ENGINE=docker codex       # explicitly select Docker
```

Use native Codex arguments. Standard arguments are forwarded without
translation. `--profile` is reserved for the wrapper's directory-based profiles.
Use profile configuration or `--model` / `-m` to select a model.

The wrapper allocates an interactive container terminal (`-it`), including
for `exec`.

The launcher preserves the host's `TERM`, `COLORTERM`, terminal identity,
multiplexer metadata, and explicit color preferences such as `NO_COLOR` and
`FORCE_COLOR`. This lets Codex use the same color capabilities as a native
launch. The image uses UTF-8 and includes Kitty's `xterm-kitty` definition
alongside additional terminal definitions.
Terminal dimensions and resize events come from the allocated TTY. Host fonts
control how glyphs look.

## Authentication

**Codex:** log in from inside the container. All profiles share
`HOST_CODEX_HOME/auth/auth.json`; sessions, plugins, and configuration remain
separate. `codex login` and `codex logout` operate on this shared account,
regardless of the selected profile. No host `~/.codex` directory is mounted
or imported, and no OpenAI API key is automatically forwarded.

Use the wrapper's `login` / `logout` commands to manage the shared account,
rather than changing accounts within a running session. Credentials use
Codex's file storage, not a host keyring. Treat the shared `auth.json` like a
password and never commit it.

**GitHub:** the wrapper separately resolves a token for `gh` in this order:

1. `CODEX_GITHUB_TOKEN`
2. `GH_TOKEN`
3. The host's `gh auth token`

GitHub credentials are optional for Codex itself, but required for the PR
workflows. They are not used to authenticate Codex.
The resolved token is passed through the engine's environment using
`-e GH_TOKEN`, keeping its value out of command-line arguments.

The host Git name, email, and read-only `~/.gitconfig` are forwarded unchanged,
so commits are authored as you, without tool-specific co-author trailers.

## Configuration

All optional, set as environment variables:

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `CONTAINER_ENGINE` | Auto-detect, preferring `podman` | Select `podman` or `docker`; an explicit choice never falls back. |
| `IMAGE_NAME` | `codex-container` | Image tag to build/run. |
| `DOCKERFILE_PATH` | `Dockerfile` next to the wrapper | Dockerfile location. |
| `HOST_CODEX_HOME` | `${XDG_DATA_HOME:-~/.local/share}/codex-cli` | Host directory for profile state and shared Codex login. |
| `CODEX_PROFILE` | `default` | Profile, overridden by `--profile`. |
| `CODEX_NTFY_TOPIC` | Empty / disabled | ntfy.sh topic for notifications. |
| `CODEX_GITHUB_TOKEN` / `GH_TOKEN` | From host `gh`, if available | GitHub token for `gh`, not Codex authentication. |

## Profiles and persistent state

Add a directory under `profiles/` next to the wrapper:

```text
profiles/<profile>/
├── command.sh
├── init.sh
├── hooks.json
├── AGENTS.md
├── config.toml
└── prompts/
    └── example.md
```

Every asset except `command.sh` falls back to the matching file in
`profiles/default/` when absent. An empty file disables that asset; an empty
hooks file removes hooks copied at startup. `{}` also represents a hooks file
with no hooks. Invalid configuration is reported by Codex rather than silently
ignored.

The selected profile's state is stored at
`HOST_CODEX_HOME/profiles/<profile>/` and mounted at `/codex-state`, with
`CODEX_HOME=/codex-state`. The shared authentication directory is mounted
separately at `/codex-auth`. A profile's `auth.json` links to the shared file
so token refreshes are shared too.

At startup, the wrapper copies `hooks.json` and `AGENTS.md` into `CODEX_HOME`
for native Codex discovery. It copies the resolved profile `config.toml` to
`CODEX_HOME/container.config.toml` and selects it using Codex's native
`--profile container`. This keeps repository-owned defaults authoritative
without overwriting Codex's private `config.toml`, plugin metadata, or hook
trust decisions. Edit the versioned profile files to change defaults.
CLI `--config` / `-c` overrides still apply.

The default status line shows the model and reasoning level, weekly limit,
remaining context, and current Git branch. It omits the container working
directory. This also applies to profiles that inherit the default configuration.

`init.sh` runs before a session starts, and may install profile-specific
plugins. An optional `command.sh` then receives the CLI arguments. The `pr`
dispatcher handles its workflow commands and passes native Codex commands
such as `resume`, `exec`, and `fork` through normally. Login/logout and the
debug shell bypass profile initialization and dispatch. Management commands
that Codex does not allow to select a native config profile (such as `plugin`
and `features`) also bypass the config overlay and profile initialization.

Sessions are profile-local. To resume a named profile's session, select that
same profile, for example `codex --profile pr resume <session-id>`.
`resume --all` includes sessions across working directories within the
selected profile.

Built-in profiles:

- `default` - native notification hooks and commit-authorship instructions.
- `mcp` - enables Kubernetes at `http://localhost:8080/mcp` and Grafana at
  `http://localhost:18000/mcp` by default. Inherits the default hooks and
  commit-authorship instructions. Start both MCP servers before launching
  `codex --profile mcp`; the profile connects to them without starting them.
- `pony` - installs the native
  [Ponytail Codex plugin](https://github.com/DietrichGebert/ponytail).
  Its enabled lifecycle hooks run without a manual trust step.
- `pr` - provides PR workflows using `codex exec`: `create`
  commits pending changes and opens/updates a draft PR without running tests;
  `describe` updates a PR's title/description and non-standard commit messages;
  `review` addresses review comments and failing CI in a temporary worktree.
  Prompts remain in `profiles/pr/prompts/*.md`, with `{{PR_LINK}}` and
  `{{EXTRA_INSTRUCTIONS}}` rendered by `command.sh`. Model and `-c` options
  go before the workflow name. `create` and `describe` (also `description`)
  default to GPT-6-Luna with high reasoning; `review` defaults to GPT-6.1-Sol
  with low reasoning.
  CLI model and reasoning overrides take precedence.

Delete only a profile's state directory to reset it without affecting the
others or the shared login.

## Notifications

```bash
export CODEX_NTFY_TOPIC="my-unique-topic-name"
```

Subscribe in the ntfy app or at `https://ntfy.sh/my-unique-topic-name`.
Leave the variable unset to disable sending.

The default profile uses native Codex `Stop`, `PreToolUse` for
`request_user_input`, and `PermissionRequest` hooks for done/waiting messages.
Hooks run asynchronously. Approval notifications normally do not fire because
the wrapper bypasses approvals. Only events exposed by Codex are covered.

The wrapper passes `--dangerously-bypass-hook-trust` on every session launch,
so enabled user, project, and plugin hooks run without manual trust, including
new or changed definitions. Hooks disabled in Codex remain disabled.
The launch directory, any `--cd` / `-C` target, and their parent directories
are marked trusted through CLI configuration defaults. This also loads their
project configuration and hooks without folder trust prompts. These defaults
apply to all profiles, including PR workflows, and leave persisted private
configuration intact. Explicit CLI `-c` overrides still take precedence.

## Permissions and engine setup

The image is shared between users; no user-specific rebuild is needed.

| Engine mode | Runtime identity |
| ----------- | ---------------- |
| Podman | `--userns=keep-id`, your UID/GID, and `--group-add keep-groups` (requires a compatible runtime such as `crun`). |
| Regular Docker | Your numeric UID/GID and supplementary groups; `--userns=host` opts out of daemon-wide `userns-remap` for correct bind-mount ownership. |
| Rootless Docker | Container UID/GID `0:0`, mapped to the unprivileged daemon user, not host root. The daemon must run as your user. |

Before launching, the wrapper probes writability and host ownership through
the workspace, selected profile state, sessions, and shared-auth mounts.
Failures preserve the engine error, stop startup, and clean temporary probes.
It does not recursively chown files, make them world-writable, mount the
Docker socket, or request privileged mode. SELinux labeling is disabled for
these mounts; host files are not relabeled with `:Z`.

For Docker socket errors, configure rootless Docker or administrator-provided
access. **Do not use sudo or make the socket world-writable**: sudo changes
Git identity, state location, and ownership. Docker-group membership grants
root-equivalent access to a regular daemon.

If `/workspace`, `/codex-state`, or `/codex-auth` is not writable, check the
reported host path's ownership and permissions. Rootless Docker cannot retain
host supplementary groups; for group-only access, use Podman with
`keep-groups`, regular Docker, or arrange direct user access.

Bypassing Codex approvals and its inner sandbox is intentional. The outer
container still has writable host mounts, credentials, and host networking
(inside the daemon's network namespace for rootless Docker).

## Launcher regression tests

```bash
python3 -B -m unittest discover -s tests -v
```

Tests use mock engines and real write probes against temporary directories,
without Docker or Podman. They also cover profile dispatch and shared
authentication setup. Rootless UID mapping is checked when Linux user
namespaces are available.
