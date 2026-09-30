# copilot-container

Run the [GitHub Copilot CLI](https://github.com/github/copilot-cli) inside a
disposable container. Your current directory is mounted as `/workspace`, your
host Git identity and GitHub token are forwarded in, and the CLI runs with
`--allow-all`. It can modify files in the mounted workspace and profile state.

Works with **Podman** or **Docker Engine** on Linux. The wrapper prefers Podman
when both are installed; set `CONTAINER_ENGINE=docker` to select Docker explicitly.

## Quickstart

```bash
git clone https://github.com/mortrevere/copilot-container.git
cd copilot-container
./copilot-container
```

Docker must be running and accessible to your user (`docker info` must succeed).
The wrapper uses your current Docker context, including a rootless context.
Use a local engine: bind-mounted paths and user IDs must refer to this host.

This is meant for using Copilot CLI without letting it install tools, write
config, or leave temporary state directly on your host machine. It is not a
perfect sandbox: Copilot still has access to the mounted workspace, forwarded
GitHub token, profile state, and network. Treat it as a convenience and safety
wrapper, not as a hardened security boundary.

Highlights:

- Works with **Podman** or **Docker Engine**, preferring Podman when available.
- Mounts the current directory at `/workspace` and keeps generated files owned
  by your host user where the container engine allows it.
- Forwards your host Git identity so commits made by Copilot are authored as
  you.
- Resolves a GitHub token from `COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, or your local
  `gh auth token`, so you can pass a more limited token if desired.
- Supports `--profile` for custom instructions, settings, hooks, skills, and
  per-profile persistent state. A `pony` profile is included as an example.
- Sends optional desktop/mobile notifications through
  [ntfy.sh](https://ntfy.sh) when Copilot is done or waiting for input.
- Includes `gh`, `uv`, and `ruff` in the image; additional tools installed
  inside the container disappear when the session ends.
- Provides `copilot bash` for debugging inside the container and
  `copilot update` for a clean image rebuild with a backup tag.
- Checks workspace and profile-state writability before launching, and does not
  mount the Docker socket or request privileged mode.

## Files

- `Dockerfile` — the container image.
- `copilot-container` — the host-side wrapper script that builds/runs the image.
- `profiles/` — built-in profiles, each containing `init.sh`, `hooks.json`, and
  `copilot-instructions.md`, and `settings.json`.

## 1. Build the image

From the directory containing these files:

```bash
podman build . -f Dockerfile -t copilot-container
# Or:
docker build . -f Dockerfile -t copilot-container
```

The wrapper also builds the image automatically on first run, so this step is
optional — but doing it once up front avoids a wait on your first invocation.

## 2. Install the wrapper

Copy the wrapper script somewhere on your `PATH` and make it executable:

```bash
install -Dm755 copilot-container ~/.local/bin/copilot-container
```

The wrapper looks for `Dockerfile` next to itself by default.
If you install the script elsewhere from the Dockerfile, point it at the
Dockerfile explicitly with `DOCKERFILE_PATH` (see below).

## 3. Add the alias

Add this one line to your `~/.bashrc` (or `~/.zshrc`) and restart your shell:

```bash
alias copilot='~/.local/bin/copilot-container'
```

## Usage

```bash
copilot                       # start an interactive Copilot session in $PWD
copilot -p "fix the failing test"  # one-shot prompt
copilot --resume              # resume from the persistent global-resume dir
copilot --profile pony        # start with the Ponytail plugin profile
copilot --profile pr create "focus on the API changes"
copilot --profile pr --resume=<session-id>  # resume a PR-profile session
copilot --profile pr describe "https://github.com/OWNER/REPO/pull/123"
copilot --profile pr review "https://github.com/OWNER/REPO/pull/123"
COPILOT_PROFILE=pony copilot  # select a profile with an environment variable
copilot bash                  # drop into a shell inside the container
copilot update                # rebuild the image with --no-cache (keeps a backup tag)
CONTAINER_ENGINE=docker copilot  # explicitly use Docker instead of Podman
```

## Authentication

The wrapper resolves a GitHub token in this order:

1. `COPILOT_GITHUB_TOKEN`
2. `GH_TOKEN`
3. `gh auth token` (i.e. your local `gh` login)

So if you already use the `gh` CLI, no extra setup is needed.

## Configuration

All optional, set as environment variables:

| Variable             | Default                          | Purpose                                            |
| -------------------- | -------------------------------- | -------------------------------------------------- |
| `CONTAINER_ENGINE`   | Auto-detect, preferring `podman` | Select `podman` or `docker`. An explicit choice never falls back. |
| `IMAGE_NAME`         | `copilot-container`              | Image tag to build/run.                            |
| `DOCKERFILE_PATH`    | `Dockerfile` next to the wrapper | Where to find the Dockerfile.                      |
| `HOST_COPILOT_HOME`  | `${XDG_DATA_HOME:-~/.local/share}/copilot-cli` | Host dir for persistent Copilot state. |
| `COPILOT_PROFILE`    | `default`                        | Profile to use, overridden by `--profile`.         |
| `COPILOT_NTFY_TOPIC` | *(empty / disabled)*             | [ntfy.sh](https://ntfy.sh) topic for notifications.|
| `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` | *(from `gh`)*     | GitHub token override.                             |

### Notifications (optional)

Set `COPILOT_NTFY_TOPIC` to an [ntfy.sh](https://ntfy.sh) topic to get a push
notification when Copilot is waiting for your input or has finished:

```bash
export COPILOT_NTFY_TOPIC="my-unique-topic-name"
```

Subscribe to the same topic in the ntfy app or at `https://ntfy.sh/my-unique-topic-name`.
Leave it unset to disable notifications entirely.

## Profiles and persistent state

Profiles become available by adding a directory beneath `profiles/` next to the
wrapper. The default profile provides these assets, and named profiles may
override any subset:

```text
profiles/<profile>/
├── command.sh
├── init.sh
├── hooks.json
├── copilot-instructions.md
├── prompts/
│   └── example.md
└── settings.json
```

The selected profile's sessions, plugins, hooks, and settings are stored on the
host at `HOST_COPILOT_HOME/profiles/<profile>/` and mounted into the container
at `/copilot-state`. The container copies `hooks.json` to `hooks/notify.json`
and `copilot-instructions.md`, and copies the resolved profile `settings.json`
into the selected state before each launch. Profile instructions are also
linked into Copilot's `$HOME/.copilot` instruction-discovery path.
`init.sh` runs inside the container before Copilot starts, so it can install
profile-specific plugins. A profile may also include `command.sh`; when present,
it runs inside the container after `init.sh` and receives the Copilot arguments.
This lets a profile turn command-style arguments into a full Copilot invocation,
such as selecting a model and passing a generated `-p` prompt.
Command-style profiles may keep long prompts in versioned Markdown files under
their own `prompts/` directory. The Markdown files are inert data; any
placeholder rendering is owned by that profile's `command.sh`.

Profile files are optional for named profiles: a missing file falls back to the
same file in `profiles/default/`; an existing empty file explicitly disables
that profile asset. `command.sh` is optional and has no fallback requirement; a
profile without it keeps the standard `copilot "$@"` argument forwarding. Hooks
and instructions are copied on every startup so profile edits take effect
immediately. The profile repository is authoritative for `settings.json`:
settings changes made within the CLI are intentionally discarded when the
session ends, while manual profile-file edits apply to the next instance.
Copilot's internal `config.json` remains only in the private profile state
directory, where its authentication and plugin metadata are not stored with the
wrapper's profile files. A blank private `config.json` is treated as
uninitialized and recreated at startup. The default
profile's `settings.json` selects Copilot's `default` theme, which uses the
terminal's native color palette rather than a Copilot-specific background.

The `default` profile retains `--resume` support through its
`global-resume/` directory, allowing its sessions to be resumed from any
repository or folder. Named profiles do not use this shared resume store: pass
the same profile used to start the session when resuming it, for example
`copilot --profile pr --resume=<session-id>` (or `copilot --profile pr --resume`
to select one). Resume arguments go straight to Copilot, bypassing any
command-style profile dispatcher.

Built-in profiles:

- `default` — the standard hooks and commit-authorship instructions.
- `pony` — installs the [Ponytail](https://github.com/DietrichGebert/ponytail)
  plugin before starting Copilot.
- `pr` — command-style PR workflows:
  - `copilot --profile pr create "[extra instruction]"` reads pending git
    changes, creates logical commits on a branch based on the configured base
    branch, and opens or updates a draft PR without running tests.
  - `copilot --profile pr describe "<PR link>" "[extra instruction]"` updates
    the PR title and description from a broader project standpoint, and rewrites
    non-standard commit messages when needed.
  - `copilot --profile pr review "<PR link>" "[extra instruction]"` addresses
    open review comments and failing CI in a temporary worktree, pushes the
    commits, replies to comments, and removes the worktree.
  The prompt prose lives in `profiles/pr/prompts/*.md`; `command.sh` renders
  `{{PR_LINK}}` and `{{EXTRA_INSTRUCTIONS}}` placeholders before invoking
  Copilot.

Delete a profile's state directory to reset that profile without affecting the
others.

## Permissions and Docker setup

The image is shared between users; no user-specific rebuild is needed. The
wrapper selects the runtime identity for each engine:

| Engine mode | Identity and bind-mount behavior |
| ----------- | -------------------------------- |
| Podman | Uses `--userns=keep-id`, your UID/GID, and `--group-add keep-groups` to retain host supplementary groups (requires a compatible runtime such as `crun`). |
| Regular Docker | Uses your numeric UID/GID and supplementary group IDs, so newly created files belong to you, not root. Uses `--userns=host` to opt this container out of daemon-wide `userns-remap`; otherwise bind mounts would use subordinate IDs. |
| Rootless Docker | Uses container UID/GID `0:0`, which maps to the unprivileged user running the daemon, **not host root**. The daemon must run as your user. Passing your host UID to a rootless container would select the wrong host identity. |

Before starting Copilot, the wrapper creates temporary files through the
workspace and state mounts and checks file ownership from the host. It also
checks the hooks and default-profile resume directories. Failed probes stop
the launch with the original engine error; temporary probes are cleaned up.
The wrapper does not recursively `chown` your files, make them world-writable,
mount the Docker socket, or request privileged mode. SELinux container labeling
is disabled for these bind mounts; host files are not relabeled with `:Z`.

If Docker reports a socket permission error, configure access for your normal
user using [rootless Docker](https://docs.docker.com/engine/security/rootless/)
or your administrator's Docker setup. Membership in the `docker` group grants
root-equivalent access to a regular Docker daemon and may require a new login
to take effect. **Do not fix this by running the wrapper with `sudo` or making
the Docker socket world-writable**: `sudo` changes the Git identity, state
location, and ownership of generated files.

If `/workspace` or `/copilot-state` is not writable, check the reported host
path's ownership, permissions, and any read-only filesystem restrictions.
Existing root-owned files from earlier runs may need a targeted ownership
repair by their owner or administrator. Rootless Docker cannot retain the
caller's host supplementary groups: for group-only access to shared checkouts,
use Podman with `keep-groups`, regular Docker, or arrange direct access for your
user with the directory owner.

`--allow-all` is intentional, but the container is not a complete security
boundary: it has writable host bind mounts, your GitHub token, and host
networking (inside the daemon's network namespace for rootless Docker).

## Launcher regression tests

```bash
python3 -B -m unittest discover -s tests -v
```

These use mock engines and run the actual write-probe shell commands against
temporary directories without requiring Docker or Podman. When Linux user
namespaces are available, they also exercise rootless UID mapping.
