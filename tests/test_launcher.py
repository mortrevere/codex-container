"""Launcher regression tests; no container engine or third-party packages needed."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.workspace = self.root / "checkout with spaces"
        self.workspace.mkdir()
        self.state = self.root / "state with spaces"
        self.log = self.root / "engine.jsonl"
        self.environment_log = self.root / "engine-env.json"
        for tool in ("bash", "dirname", "mkdir", "chmod", "mktemp", "rm", "rmdir", "date", "python3", "cp", "ln", "cat"):
            self.bin.joinpath(tool).symlink_to(shutil.which(tool))
        self.script("id", """#!/usr/bin/env bash
case "$1" in
  -u) echo 12001 ;;
  -g) echo 13001 ;;
  -G) echo "13001 14001 15001" ;;
  *) exit 1 ;;
esac
""")
        self.script("git", "#!/usr/bin/env bash\nexit 1\n")
        self.env = {
            "PATH": str(self.bin),
            "HOME": str(self.home),
            "HOST_CODEX_HOME": str(self.state),
            "CODEX_GITHUB_TOKEN": "test-token",
            "ENGINE_LOG": str(self.log),
            "ENGINE_ENV_LOG": str(self.environment_log),
        }

    def script(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def engine(self, name):
        path = self.bin / name
        shutil.copyfile(ROOT / "tests/fake-engine.py", path)
        path.chmod(0o755)

    def launch(self, *args, **env):
        result = subprocess.run(
            [str(ROOT / "codex-container"), *args],
            cwd=self.workspace,
            env={**self.env, **env},
            text=True,
            capture_output=True,
        )
        self.calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.workspace.glob(".codex-write-test.*")))
        self.assertFalse(list(self.state.rglob(".codex-write-test.*")))

    def runs(self):
        return [call for call in self.calls if call[1] == "run"]

    def values(self, args, option):
        return [args[index + 1] for index, arg in enumerate(args) if arg == option]

    def test_docker_autodetection_preserves_ids_and_groups_in_every_run(self):
        self.engine("docker")
        result = self.launch("exec", "a prompt with spaces")
        self.assert_success(result)
        self.assertEqual(len(self.runs()), 4)
        for run in self.runs():
            self.assertEqual(run[0], "docker")
            self.assertIn("--userns=host", run)
            self.assertEqual(self.values(run, "--user"), ["12001:13001"])
            self.assertEqual(self.values(run, "--group-add"), ["14001", "15001"])
            self.assertEqual(self.values(run, "--security-opt"), ["label=disable"])
            self.assertNotIn("--userns=keep-id", run)
            self.assertNotIn("keep-groups", run)
            self.assertFalse(any(arg.endswith(":Z") for arg in run))
        self.assertEqual(self.runs()[-1][-4:], ["bash", "/codex-container-init", "exec", "a prompt with spaces"])

    def test_podman_remains_preferred(self):
        self.engine("podman")
        self.engine("docker")
        self.assert_success(self.launch())
        self.assertTrue(all(call[0] == "podman" for call in self.calls))
        for run in self.runs():
            self.assertIn("--userns=keep-id", run)
            self.assertEqual(self.values(run, "--group-add"), ["keep-groups"])
            self.assertEqual(self.values(run, "--user"), ["12001:13001"])

    def test_explicit_docker_overrides_podman(self):
        self.engine("podman")
        self.engine("docker")
        self.assert_success(self.launch(CONTAINER_ENGINE="docker"))
        self.assertTrue(all(call[0] == "docker" for call in self.calls))

    def test_explicit_podman(self):
        self.engine("podman")
        self.engine("docker")
        self.assert_success(self.launch(CONTAINER_ENGINE="podman"))
        self.assertTrue(all(call[0] == "podman" for call in self.calls))

    def test_rootless_docker_uses_namespace_root_not_host_ids(self):
        self.engine("docker")
        self.assert_success(self.launch(SECURITY_OPTIONS='["name=seccomp,profile=builtin","name=rootless"]'))
        for run in self.runs():
            self.assertEqual(self.values(run, "--user"), ["0:0"])
            self.assertNotIn("--userns=host", run)
            self.assertNotIn("--group-add", run)

    def test_rootless_probes_use_real_user_namespace(self):
        unshare = shutil.which("unshare")
        if not unshare:
            self.skipTest("unshare is unavailable")
        supported = subprocess.run(
            [unshare, "--user", "--map-root-user", "true"],
            capture_output=True,
        )
        if supported.returncode:
            self.skipTest("user namespaces are unavailable")
        self.bin.joinpath("unshare").symlink_to(unshare)
        self.engine("docker")
        self.assert_success(self.launch(SECURITY_OPTIONS='["name=rootless"]', PROBE_USERNS="1"))

    def test_remapped_docker_uses_host_namespace(self):
        self.engine("docker")
        self.assert_success(self.launch(SECURITY_OPTIONS='["name=userns"]'))
        for run in self.runs():
            self.assertIn("--userns=host", run)
            self.assertEqual(self.values(run, "--user"), ["12001:13001"])

    def test_missing_engines(self):
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("neither podman nor docker", result.stderr)

    def test_missing_requested_engine_does_not_fall_back(self):
        self.engine("podman")
        result = self.launch(CONTAINER_ENGINE="docker")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("docker not found", result.stderr)
        self.assertEqual(self.calls, [])

    def test_invalid_engine(self):
        result = self.launch(CONTAINER_ENGINE="something-else")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONTAINER_ENGINE must be", result.stderr)

    def test_daemon_failure_is_not_misdiagnosed_as_missing_image(self):
        self.engine("docker")
        result = self.launch(FAIL_INFO="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("permission denied connecting to Docker socket", result.stderr)
        self.assertIn("cannot access the Docker daemon", result.stderr)
        self.assertIn("do not run this wrapper with sudo", result.stderr)
        self.assertEqual([call[1] for call in self.calls], ["info"])
        self.assertFalse(self.state.exists())

    def test_first_run_build_uses_selected_engine(self):
        for engine in ("docker", "podman"):
            with self.subTest(engine=engine):
                self.engine(engine)
                if self.log.exists():
                    self.log.unlink()
                self.assert_success(self.launch(CONTAINER_ENGINE=engine, IMAGE_MISSING="1"))
                builds = [call for call in self.calls if call[1] == "build"]
                self.assertEqual(len(builds), 1)
                self.assertEqual(builds[0][0], engine)
                self.assertIn(str(ROOT / "Dockerfile"), builds[0])

    def test_update_tags_and_rebuilds_without_starting_a_session(self):
        for engine in ("docker", "podman"):
            with self.subTest(engine=engine):
                self.engine(engine)
                if self.log.exists():
                    self.log.unlink()
                self.assert_success(self.launch("update", CONTAINER_ENGINE=engine, CODEX_GITHUB_TOKEN=""))
                commands = [call[1] for call in self.calls]
                self.assertEqual(commands, ["info", "tag", "build"] if engine == "docker" else ["tag", "build"])
                self.assertIn("--no-cache", self.calls[-1])
                self.assertFalse(self.state.exists())

    def test_workspace_failure_preserves_engine_error_and_cleans_probe(self):
        self.engine("docker")
        result = self.launch(FAIL_MOUNT="/workspace")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mock engine: bind mount permission denied", result.stderr)
        self.assertIn("/workspace is not writable", result.stderr)
        self.assertFalse(list(self.workspace.glob(".codex-write-test.*")))
        self.assertEqual(len(self.runs()), 1)

    @unittest.skipIf(os.geteuid() == 0, "root can bypass directory permissions")
    def test_host_workspace_must_be_writable(self):
        self.engine("docker")
        self.workspace.chmod(0o500)
        self.addCleanup(self.workspace.chmod, 0o700)
        result = self.launch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot prepare /workspace write probe", result.stderr)
        self.assertEqual(self.runs(), [])

    def test_state_failure_stops_before_session(self):
        self.engine("docker")
        result = self.launch(FAIL_MOUNT="/codex-state")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/codex-state is not writable", result.stderr)
        self.assertFalse(list(self.state.rglob(".codex-write-test.*")))
        self.assertEqual(len(self.runs()), 2)

    def test_shared_auth_failure_stops_before_session(self):
        self.engine("docker")
        result = self.launch(FAIL_MOUNT="/codex-auth")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/codex-auth is not writable", result.stderr)
        self.assertFalse(list(self.state.rglob(".codex-write-test.*")))
        self.assertEqual(len(self.runs()), 3)

    def test_probes_must_be_visible_and_owned_on_host(self):
        self.engine("docker")
        result = self.launch(SKIP_PROBE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not create a file owned by your host user", result.stderr)
        self.assertFalse(list(self.workspace.glob(".codex-write-test.*")))

    def test_existing_write_test_files_are_not_clobbered(self):
        self.engine("docker")
        sessions = self.state / "profiles/default/sessions"
        sessions.mkdir(parents=True)
        old_probe = sessions / ".codex-write-test"
        old_probe.write_text("keep me")
        self.assert_success(self.launch())
        self.assertEqual(old_probe.read_text(), "keep me")

    def test_relative_state_path_is_a_bind_mount_not_a_named_volume(self):
        self.engine("docker")
        self.assert_success(self.launch(HOST_CODEX_HOME="relative-state"))
        mounts = self.values(self.runs()[-1], "-v")
        self.assertIn(f"{self.workspace}/relative-state/profiles/default:/codex-state", mounts)
        self.assertIn(f"{self.workspace}/relative-state/auth:/codex-auth", mounts)

    def test_resume_profile_and_bash_arguments(self):
        self.engine("docker")
        self.assert_success(self.launch("resume", "--all"))
        self.assertEqual(self.runs()[-1][-4:], [
            "bash", "/codex-container-init", "resume", "--all",
        ])
        self.assert_success(self.launch("--profile", "pony", "resume", "--last"))
        self.assertIn(f"{self.state}/profiles/pony:/codex-state", self.runs()[-1])
        self.assertIn(f"{self.state}/auth:/codex-auth", self.runs()[-1])
        self.assert_success(self.launch("bash"))
        self.assertEqual(self.runs()[-1][-3:], ["bash", "/codex-container-init", "bash"])

    def test_native_resume_arguments_are_preserved(self):
        self.engine("docker")
        for args, forwarded in (
            (("--profile", "pr", "resume", "session-id"), ["resume", "session-id"]),
            (("resume", "--profile", "pr", "session-id"), ["resume", "session-id"]),
            (("--profile=pr", "resume"), ["resume"]),
            (("--profile", "pr", "resume", "--last"), ["resume", "--last"]),
            (("--profile", "pr", "exec", "resume", "--all", "--last"), ["exec", "resume", "--all", "--last"]),
        ):
            with self.subTest(args=args):
                self.assert_success(self.launch(*args))
                run = self.runs()[-1]
                self.assertIn(f"{self.state}/profiles/pr:/codex-state", run)
                self.assertIn("CODEX_PROFILE_COMMAND=/codex-profiles/pr/command.sh", run)
                self.assertEqual(run[-(2 + len(forwarded)):], [
                    "bash", "/codex-container-init", *forwarded,
                ])

    def test_profile_command_is_wired_when_present(self):
        self.engine("docker")
        self.assert_success(self.launch("--profile", "pr", "describe", "https://github.com/o/r/pull/1"))
        run = self.runs()[-1]
        self.assertIn(f"{self.state}/profiles/pr:/codex-state", run)
        self.assertIn("CODEX_PROFILE_COMMAND=/codex-profiles/pr/command.sh", run)
        self.assertEqual(run[-4:], ["bash", "/codex-container-init", "describe", "https://github.com/o/r/pull/1"])

    def test_profile_without_command_keeps_standard_forwarding(self):
        self.engine("docker")
        self.assert_success(self.launch("--profile", "pony", "hello"))
        run = self.runs()[-1]
        self.assertIn("CODEX_PROFILE_COMMAND=", run)
        self.assertEqual(run[-3:], ["bash", "/codex-container-init", "hello"])

    def test_pr_profile_command_generates_codex_invocations(self):
        commands = [
            ("create", ("create", "focus on API"), "Read the pending git changes", "focus on API"),
            ("describe", ("describe", "https://github.com/o/r/pull/1", "mention migrations"), "Read the PR at https://github.com/o/r/pull/1", "mention migrations"),
            ("review", ("review", "https://github.com/o/r/pull/2", "prioritize CI"), "See the review comments on https://github.com/o/r/pull/2", "prioritize CI"),
        ]

        for name, args, prompt_start, extra in commands:
            with self.subTest(name=name):
                args_file = self.root / f"{name}-args.json"
                self.script("codex", """#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ["CODEX_ARGS_FILE"], "w") as file:
    json.dump(sys.argv[1:], file)
""")
                result = subprocess.run(
                    ["bash", str(ROOT / "profiles/pr/command.sh"), *args],
                    env={**self.env, "CODEX_ARGS_FILE": str(args_file)},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                codex_args = json.loads(args_file.read_text())
                self.assertEqual(codex_args[:-1], [
                    "exec", "--profile", "container",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "--dangerously-bypass-hook-trust",
                    "-c", 'cli_auth_credentials_store="file"',
                    "-c", 'projects."/workspace".trust_level="trusted"',
                ])
                self.assertIn(prompt_start, codex_args[-1])
                self.assertIn(extra, codex_args[-1])
                self.assertIn("Additional user instruction:\n", codex_args[-1])
                self.assertNotIn("{{PR_LINK}}", codex_args[-1])
                self.assertNotIn("{{EXTRA_INSTRUCTIONS}}", codex_args[-1])

    def test_git_identity_and_readonly_config_are_forwarded(self):
        self.engine("docker")
        self.script("git", """#!/usr/bin/env bash
case "$3" in
  user.name) echo "Test Author" ;;
  user.email) echo "test@example.invalid" ;;
  *) exit 1 ;;
esac
""")
        (self.home / ".gitconfig").write_text("[user]\n\tname = Test Author\n")
        self.assert_success(self.launch())
        run = self.runs()[-1]
        self.assertIn("GIT_AUTHOR_NAME=Test Author", run)
        self.assertIn("GIT_COMMITTER_EMAIL=test@example.invalid", run)
        self.assertIn(f"{self.home}/.gitconfig:/tmp/host-gitconfig:ro", run)
        self.assertIn("HOME=/tmp/home", run)
        self.assertIn("CODEX_HOME=/codex-state", run)
        self.assertIn("CODEX_AUTH_HOME=/codex-auth", run)

    def test_github_credentials_are_optional_and_not_codex_credentials(self):
        self.engine("docker")
        self.assert_success(self.launch(CODEX_GITHUB_TOKEN=""))
        run = self.runs()[-1]
        self.assertIn("GH_TOKEN", run)
        self.assertEqual(json.loads(self.environment_log.read_text())["GH_TOKEN"], "")
        self.assertFalse(any(arg.startswith("OPENAI_API_KEY=") for arg in run))
        self.assert_success(self.launch(CODEX_GITHUB_TOKEN="", GH_TOKEN="gh-token"))
        self.assertEqual(json.loads(self.environment_log.read_text())["GH_TOKEN"], "gh-token")
        self.assert_success(self.launch(GH_TOKEN="gh-token"))
        self.assertEqual(json.loads(self.environment_log.read_text())["GH_TOKEN"], "test-token")
        for run in self.runs():
            self.assertFalse(any(arg.startswith("GH_TOKEN=") for arg in run))
            self.assertFalse(any(token in arg for token in ("test-token", "gh-token") for arg in run))

    def test_host_gh_token_resolution_and_failure_warning(self):
        self.engine("docker")
        self.script("gh", "#!/usr/bin/env bash\nprintf 'host-token\\n'\n")
        self.assert_success(self.launch(CODEX_GITHUB_TOKEN=""))
        self.assertIn("GH_TOKEN", self.runs()[-1])
        self.assertEqual(json.loads(self.environment_log.read_text())["GH_TOKEN"], "host-token")
        self.assertFalse(any("host-token" in arg for run in self.runs() for arg in run))
        self.script("gh", "#!/usr/bin/env bash\necho 'not logged in' >&2\nexit 1\n")
        result = self.launch(CODEX_GITHUB_TOKEN="")
        self.assert_success(result)
        self.assertIn("warning: no GitHub token available", result.stderr)
        self.assertIn("not logged in", result.stderr)
        self.assertEqual(json.loads(self.environment_log.read_text())["GH_TOKEN"], "")

    def test_host_terminal_capabilities_reach_both_engines(self):
        terminal = {
            "TERM": "xterm-kitty",
            "COLORTERM": "truecolor",
            "TERM_PROGRAM": "kitty",
            "TERM_PROGRAM_VERSION": "0.43.0",
            "KITTY_WINDOW_ID": "42",
            "COLORFGBG": "15;0",
            "TMUX": "/tmp/tmux-12001/default,123,0",
            "TMUX_PANE": "%1",
        }
        for engine in ("docker", "podman"):
            with self.subTest(engine=engine):
                self.engine(engine)
                self.assert_success(self.launch(CONTAINER_ENGINE=engine, **terminal))
                run = self.runs()[-1]
                passed = self.values(run, "-e")
                captured = json.loads(self.environment_log.read_text())
                self.assertIn("-it", run)
                self.assertIn("TERM=xterm-kitty", passed)
                for name, value in terminal.items():
                    self.assertEqual(captured[name], value)
                    if name != "TERM":
                        self.assertIn(name, passed)
                self.assertNotIn("NO_COLOR", passed)
                self.assertNotIn("FORCE_COLOR", passed)
                self.assertNotIn("COLUMNS", passed)
                self.assertNotIn("LINES", passed)

    def test_terminal_color_preferences_are_preserved(self):
        self.engine("docker")
        for preferences in ({"NO_COLOR": "1"}, {"FORCE_COLOR": "3"},
                            {"TERM": "dumb", "COLORTERM": ""}):
            with self.subTest(preferences=preferences):
                self.assert_success(self.launch(**preferences))
                captured = json.loads(self.environment_log.read_text())
                passed = self.values(self.runs()[-1], "-e")
                for name, value in preferences.items():
                    self.assertEqual(captured[name], value)
                    self.assertIn(f"TERM={value}" if name == "TERM" else name, passed)

    def test_profile_environment_and_validation(self):
        self.engine("docker")
        self.assert_success(self.launch(CODEX_PROFILE="pr"))
        self.assertIn(f"{self.state}/profiles/pr:/codex-state", self.runs()[-1])
        self.assert_success(self.launch("--profile=default", CODEX_PROFILE="pr"))
        self.assertIn(f"{self.state}/profiles/default:/codex-state", self.runs()[-1])
        for args in (("--profile",), ("--profile=",), ("--profile", "../default"), ("--profile", "missing")):
            with self.subTest(args=args):
                self.assertNotEqual(self.launch(*args).returncode, 0)

    def test_arguments_after_separator_are_not_wrapper_options(self):
        self.engine("docker")
        self.assert_success(self.launch("exec", "--", "--profile", "is prompt text"))
        self.assertEqual(self.runs()[-1][-4:], ["exec", "--", "--profile", "is prompt text"])

    def run_init(self, *args, profile="default", **env):
        profile_state = self.state / "profiles" / profile
        auth = self.state / "auth"
        profile_state.mkdir(parents=True, exist_ok=True)
        auth.mkdir(parents=True, exist_ok=True)
        calls_file = self.root / "codex.jsonl"
        self.script("codex", """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
home = Path(os.environ["CODEX_HOME"])
args = sys.argv[1:]
with open(os.environ["CODEX_CALLS_FILE"], "a") as file:
    file.write(json.dumps({"home": str(home), "args": args}) + "\\n")
if os.environ.get("FAIL_CODEX"):
    sys.exit(7)
if "login" in args and "status" not in args:
    (home / "auth.json").unlink(missing_ok=True)
    (home / "auth.json").write_text('{"OPENAI_API_KEY": "fake-shared-key"}')
elif "logout" in args:
    (home / "auth.json").unlink(missing_ok=True)
elif "--profile" in args:
    (home / "auth.json").write_text('{"OPENAI_API_KEY": "fake-refreshed-key"}')
""")
        init_env = {
            **self.env,
            "CODEX_HOME": str(profile_state),
            "CODEX_AUTH_HOME": str(auth),
            "CODEX_CALLS_FILE": str(calls_file),
            "CODEX_PROFILE_COMMAND": str(ROOT / "profiles/pr/command.sh") if profile == "pr" else "",
            "CODEX_PROFILE_INIT": str(ROOT / "profiles" / profile / "init.sh") if (ROOT / "profiles" / profile / "init.sh").exists() else str(ROOT / "profiles/default/init.sh"),
        }
        for name, filename in (("HOOKS", "hooks.json"), ("INSTRUCTIONS", "AGENTS.md"), ("CONFIG", "config.toml")):
            asset = ROOT / "profiles" / profile / filename
            if not asset.exists():
                asset = ROOT / "profiles/default" / filename
            init_env[f"CODEX_PROFILE_{name}"] = str(asset)
        result = subprocess.run(
            ["bash", str(ROOT / "codex-container-init"), *args],
            cwd=self.workspace,
            env={**init_env, **env},
            text=True,
            capture_output=True,
        )
        self.codex_calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
        return result

    def test_init_loads_native_assets_without_overwriting_private_config(self):
        state = self.state / "profiles/default"
        state.mkdir(parents=True)
        private_config = state / "config.toml"
        private_config.write_text('[hooks.state.example]\ntrusted_hash = "keep-me"\n')
        result = self.run_init("exec", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(private_config.read_text(), '[hooks.state.example]\ntrusted_hash = "keep-me"\n')
        for filename, copied in (("config.toml", "container.config.toml"), ("AGENTS.md", "AGENTS.md"), ("hooks.json", "hooks.json")):
            self.assertEqual((state / copied).read_bytes(), (ROOT / "profiles/default" / filename).read_bytes())
        self.assertEqual(self.codex_calls[-1]["args"][:7], [
            "--profile", "container", "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "-c", 'cli_auth_credentials_store="file"', "-c",
        ])
        self.assertEqual(self.codex_calls[-1]["args"][-2:], ["exec", "hello"])

    def test_sessions_trust_hooks_and_launch_folders_across_profiles(self):
        import tomllib

        target = self.workspace / 'folder with "quotes" and café'
        target.mkdir()
        for profile in ("default", "pony", "pr"):
            for command in (("hello",), ("exec", "hello"), ("resume", "--last"),
                            ("--cd", str(target), "hello"),
                            ("-C", target.name, "hello"),
                            ("exec", f"--cd={target}", "hello"),
                            (f"-C{target}", "hello")):
                with self.subTest(profile=profile, command=command):
                    result = self.run_init(*command, profile=profile)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    args = self.codex_calls[-1]["args"]
                    self.assertIn("--dangerously-bypass-hook-trust", args)
                    trust = next(arg for arg in args if arg.startswith("projects="))
                    projects = tomllib.loads(trust)["projects"]
                    self.assertEqual(projects[str(self.workspace)]["trust_level"], "trusted")
                    if any(arg.startswith(("-C", "--cd")) for arg in command):
                        self.assertEqual(projects[str(target)]["trust_level"], "trusted")
                    for parent in self.workspace.parents:
                        self.assertEqual(projects[str(parent)]["trust_level"], "trusted")

        result = self.run_init("create", "keep it small", profile="pr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dangerously-bypass-hook-trust", self.codex_calls[-1]["args"])
        self.assertTrue(any(arg.startswith("projects=") for arg in self.codex_calls[-1]["args"]))

    def test_login_refresh_and_logout_share_auth_across_profiles(self):
        for profile in ("default", "pr"):
            with self.subTest(profile=profile):
                result = self.run_init("login", "--device-auth", profile=profile)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.codex_calls[-1]["home"], str(self.state / "auth"))
                self.assertEqual(self.codex_calls[-1]["args"], [
                    "-c", 'cli_auth_credentials_store="file"', "login", "--device-auth",
                ])
        for profile in ("default", "pr"):
            result = self.run_init("resume", "--last", profile=profile)
            self.assertEqual(result.returncode, 0, result.stderr)
            auth_link = self.state / "profiles" / profile / "auth.json"
            self.assertTrue(auth_link.is_symlink())
            self.assertEqual(auth_link.resolve(), self.state / "auth/auth.json")
            self.assertEqual(json.loads(auth_link.read_text())["OPENAI_API_KEY"], "fake-refreshed-key")
        result = self.run_init("logout", profile="pr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / "auth/auth.json").exists())
        self.assertFalse((self.state / "profiles/default/auth.json").exists())

    def test_shared_login_is_recognized_after_native_config_options(self):
        args = ("-c", "check_for_update_on_startup=false", "login", "--device-auth")
        result = self.run_init(*args, profile="pr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.codex_calls[-1]["home"], str(self.state / "auth"))
        self.assertEqual(self.codex_calls[-1]["args"][-len(args):], list(args))

    def test_empty_hooks_asset_removes_previous_hooks(self):
        self.assertEqual(self.run_init("--help").returncode, 0)
        empty_hooks = self.root / "empty-hooks.json"
        empty_hooks.touch()
        result = self.run_init("--help", CODEX_PROFILE_HOOKS=str(empty_hooks))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.state / "profiles/default/hooks.json").exists())

    def test_pr_model_override_and_native_commands(self):
        result = self.run_init("--model", "test-model", "-c", 'model_reasoning_effort="low"', "create", "keep it small", profile="pr")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = self.codex_calls[-1]["args"]
        self.assertEqual(args[0], "exec")
        self.assertEqual(args[-5:-1], ["--model", "test-model", "-c", 'model_reasoning_effort="low"'])
        self.assertIn("keep it small", args[-1])
        for command in (("resume", "id"), ("exec", "resume", "--last"), ("fork", "--all"), ("--help",)):
            with self.subTest(command=command):
                result = self.run_init(*command, profile="pr")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.codex_calls[-1]["args"][-len(command):], list(command))

    def test_pr_missing_link_and_option_value_are_errors(self):
        for args in (("describe",), ("review", ""), ("--model",)):
            with self.subTest(args=args):
                result = self.run_init(*args, profile="pr")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertTrue(result.stderr)

    def test_pony_uses_native_codex_plugin_and_propagates_install_failures(self):
        result = self.run_init("hello", profile="pony")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["args"] for call in self.codex_calls[:2]], [
            ["plugin", "marketplace", "add", "DietrichGebert/ponytail"],
            ["plugin", "add", "ponytail@ponytail"],
        ])
        result = self.run_init("hello", profile="pony", FAIL_CODEX="1")
        self.assertEqual(result.returncode, 7)

    def test_bash_debug_shell_bypasses_plugin_and_command_dispatch(self):
        result = self.run_init("bash", "-c", 'printf "%s" "$CODEX_HOME"', profile="pony")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(self.state / "profiles/pony"))
        self.assertEqual(self.codex_calls, [])

    def test_management_commands_do_not_receive_unsupported_profile_flag(self):
        for args in (
            ("features", "list"), ("plugin", "list"), ("completion", "bash"),
            ("debug", "models", "--bundled"),
            ("-c", "check_for_update_on_startup=false", "features", "list"),
        ):
            with self.subTest(args=args):
                result = self.run_init(*args, profile="pr")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.codex_calls[-1]["args"], [
                    "-c", 'cli_auth_credentials_store="file"', *args,
                ])
        result = self.run_init("plugin", "list", profile="pony")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.codex_calls[-1]["args"], [
            "-c", 'cli_auth_credentials_store="file"', "plugin", "list",
        ])


if __name__ == "__main__":
    unittest.main()
