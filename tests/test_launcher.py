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
        for tool in ("bash", "dirname", "mkdir", "chmod", "mktemp", "rm", "rmdir", "date", "python3"):
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
            "HOST_COPILOT_HOME": str(self.state),
            "COPILOT_GITHUB_TOKEN": "test-token",
            "ENGINE_LOG": str(self.log),
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
            [str(ROOT / "copilot-container"), *args],
            cwd=self.workspace,
            env={**self.env, **env},
            text=True,
            capture_output=True,
        )
        self.calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list(self.workspace.glob(".copilot-write-test.*")))
        self.assertFalse(list(self.state.rglob(".copilot-write-test.*")))

    def runs(self):
        return [call for call in self.calls if call[1] == "run"]

    def values(self, args, option):
        return [args[index + 1] for index, arg in enumerate(args) if arg == option]

    def test_docker_autodetection_preserves_ids_and_groups_in_every_run(self):
        self.engine("docker")
        result = self.launch("-p", "a prompt with spaces")
        self.assert_success(result)
        self.assertEqual(len(self.runs()), 3)
        for run in self.runs():
            self.assertEqual(run[0], "docker")
            self.assertIn("--userns=host", run)
            self.assertEqual(self.values(run, "--user"), ["12001:13001"])
            self.assertEqual(self.values(run, "--group-add"), ["14001", "15001"])
            self.assertEqual(self.values(run, "--security-opt"), ["label=disable"])
            self.assertNotIn("--userns=keep-id", run)
            self.assertNotIn("keep-groups", run)
            self.assertFalse(any(arg.endswith(":Z") for arg in run))
        self.assertEqual(self.runs()[-1][-3:], ["--allow-all", "-p", "a prompt with spaces"])

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
                self.assert_success(self.launch("update", CONTAINER_ENGINE=engine, COPILOT_GITHUB_TOKEN=""))
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
        self.assertFalse(list(self.workspace.glob(".copilot-write-test.*")))
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
        result = self.launch(FAIL_MOUNT="/copilot-state")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/copilot-state is not writable", result.stderr)
        self.assertFalse(list(self.state.rglob(".copilot-write-test.*")))
        self.assertEqual(len(self.runs()), 2)

    def test_probes_must_be_visible_and_owned_on_host(self):
        self.engine("docker")
        result = self.launch(SKIP_PROBE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not create a file owned by your host user", result.stderr)
        self.assertFalse(list(self.workspace.glob(".copilot-write-test.*")))

    def test_existing_write_test_files_are_not_clobbered(self):
        self.engine("docker")
        hooks = self.state / "profiles/default/hooks"
        hooks.mkdir(parents=True)
        old_probe = hooks / ".copilot-write-test"
        old_probe.write_text("keep me")
        self.assert_success(self.launch())
        self.assertEqual(old_probe.read_text(), "keep me")

    def test_relative_state_path_is_a_bind_mount_not_a_named_volume(self):
        self.engine("docker")
        self.assert_success(self.launch(HOST_COPILOT_HOME="relative-state"))
        mounts = self.values(self.runs()[-1], "-v")
        self.assertIn(f"{self.workspace}/relative-state/profiles/default:/copilot-state", mounts)

    def test_resume_profile_and_bash_arguments(self):
        self.engine("docker")
        self.assert_success(self.launch("--resume"))
        self.assertEqual(self.runs()[-1][-8:], [
            "bash", "/copilot-container-init", "--allow-all",
            "-C", "/copilot-state/global-resume", "--add-dir", "/workspace", "--resume",
        ])
        self.assertIn("/copilot-state/global-resume", self.runs()[-1])
        self.assertIn("/copilot-state/global-resume", self.runs()[1])
        self.assert_success(self.launch("--profile", "pony", "--resume"))
        self.assertNotIn("/copilot-state/global-resume", self.runs()[-1])
        self.assertIn(f"{self.state}/profiles/pony:/copilot-state", self.runs()[-1])
        self.assert_success(self.launch("bash"))
        self.assertEqual(self.runs()[-1][-2:], ["copilot-container", "bash"])

    def test_resume_bypasses_profile_command_and_preserves_arguments(self):
        self.engine("docker")
        for args, forwarded in (
            (("--profile", "pr", "--resume=session-id"), ["--resume=session-id"]),
            (("--resume=session-id", "--profile", "pr"), ["--resume=session-id"]),
            (("--profile=pr", "--resume"), ["--resume"]),
            (("--profile", "pr", "-r=session-id"), ["-r=session-id"]),
            (("--profile", "pr", "-r", "session-id"), ["-r", "session-id"]),
        ):
            with self.subTest(args=args):
                self.assert_success(self.launch(*args))
                run = self.runs()[-1]
                self.assertIn(f"{self.state}/profiles/pr:/copilot-state", run)
                self.assertIn("COPILOT_PROFILE_COMMAND=", run)
                self.assertNotIn("COPILOT_PROFILE_COMMAND=/copilot-profiles/pr/command.sh", run)
                self.assertNotIn("/copilot-state/global-resume", run)
                self.assertEqual(run[-(3 + len(forwarded)):], [
                    "bash", "/copilot-container-init", "--allow-all", *forwarded,
                ])

    def test_profile_command_is_wired_when_present(self):
        self.engine("docker")
        self.assert_success(self.launch("--profile", "pr", "describe", "https://github.com/o/r/pull/1"))
        run = self.runs()[-1]
        self.assertIn(f"{self.state}/profiles/pr:/copilot-state", run)
        self.assertIn("COPILOT_PROFILE_COMMAND=/copilot-profiles/pr/command.sh", run)
        self.assertEqual(run[-5:], ["bash", "/copilot-container-init", "--allow-all", "describe", "https://github.com/o/r/pull/1"])

    def test_profile_without_command_keeps_standard_forwarding(self):
        self.engine("docker")
        self.assert_success(self.launch("--profile", "pony", "-p", "hello"))
        run = self.runs()[-1]
        self.assertIn("COPILOT_PROFILE_COMMAND=", run)
        self.assertEqual(run[-5:], ["bash", "/copilot-container-init", "--allow-all", "-p", "hello"])

    def test_pr_profile_command_generates_copilot_invocations(self):
        commands = [
            ("create", ("create", "focus on API"), "gpt-5.4", "Read the pending git changes", "focus on API"),
            ("describe", ("describe", "https://github.com/o/r/pull/1", "mention migrations"), "gpt-5.5", "Read the PR at https://github.com/o/r/pull/1", "mention migrations"),
            ("review", ("review", "https://github.com/o/r/pull/2", "prioritize CI"), "gemini-3.8-flash", "See the review comments on https://github.com/o/r/pull/2", "prioritize CI"),
        ]

        for name, args, model, prompt_start, extra in commands:
            with self.subTest(name=name):
                args_file = self.root / f"{name}-args.json"
                self.script("copilot", f"""#!/usr/bin/env python3
import json
import os
import sys
with open(os.environ["COPILOT_ARGS_FILE"], "w") as file:
    json.dump(sys.argv[1:], file)
""")
                result = subprocess.run(
                    ["bash", str(ROOT / "profiles/pr/command.sh"), "--allow-all", *args],
                    env={**self.env, "COPILOT_ARGS_FILE": str(args_file)},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                copilot_args = json.loads(args_file.read_text())
                self.assertEqual(copilot_args[:4], ["--allow-all", "--model", model, "-p"])
                self.assertIn(prompt_start, copilot_args[4])
                self.assertIn(extra, copilot_args[4])
                self.assertIn("Additional user instruction:\n", copilot_args[4])
                self.assertNotIn("{{PR_LINK}}", copilot_args[4])
                self.assertNotIn("{{EXTRA_INSTRUCTIONS}}", copilot_args[4])

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
        self.assertIn("COPILOT_HOME=/copilot-state", run)


if __name__ == "__main__":
    unittest.main()
