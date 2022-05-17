#!/usr/bin/env python
#
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import argparse
import os
import subprocess
import tempfile
import time
from typing import List, Optional
from uuid import UUID, uuid4

from cleanup_ad import delete_current_user_app_registrations
from github_client import GithubClient


def venv_path(base: str, name: str) -> str:
    for subdir in ["bin", "Scripts"]:
        path = os.path.join(base, subdir, name)
        for ext in ["", ".exe"]:
            path += ext
            if os.path.exists(path):
                return path
    raise Exception("missing venv")


class Deployer:
    def __init__(
        self,
        *,
        pr: int,
        branch: str,
        instance: str,
        region: str,
        subscription_id: Optional[str],
        authority: Optional[str],
        skip_tests: bool,
        test_args: List[str],
        repo: str,
        unattended: bool,
        repo_upgrade_from: str,
        branch_upgrade_from: str,
        pr_upgrade_from: int,
        skip_upgrade_test: bool,
    ):
        self.downloader = GithubClient()
        self.pr = pr
        self.branch = branch
        self.instance = instance
        self.region = region
        self.subscription_id = subscription_id
        self.skip_tests = skip_tests
        self.test_args = test_args or []
        self.repo = repo
        self.unattended = unattended
        self.client_id: Optional[str] = None
        self.client_secret: Optional[str] = None
        self.authority = authority
        self.repo_upgrade_from = repo_upgrade_from
        self.branch_upgrade_from = branch_upgrade_from
        self.pr_upgrade_from = pr_upgrade_from
        self.skip_upgrade_test = skip_upgrade_test

    def merge(self) -> None:
        if self.pr:
            self.downloader.merge_pr(self.branch, self.pr)

    def deploy(self, filename: str) -> None:
        print(f"deploying {filename} to {self.instance}")
        venv = "deploy-venv"
        subprocess.check_call(f"python -mvenv {venv}", shell=True)
        pip = venv_path(venv, "pip")
        py = venv_path(venv, "python")
        config = os.path.join(os.getcwd(), "config.json")
        commands = [
            ("extracting release-artifacts", f"unzip -qq {filename}"),
            ("extracting deployment", "unzip -qq onefuzz-deployment*.zip"),
            ("installing wheel", f"{pip} install -q wheel"),
            ("installing prereqs", f"{pip} install -q -r requirements.txt"),
            (
                "replace deploy",
                "cp -f /mnt/c/Users/statis/Documents/repos/onefuzz/onefuzz/src/utils/check-pr/deploy.py .",
            ),
            (
                "running deployment",
                (
                    f"{py} deploy.py {self.region} "
                    f"{self.instance} {self.instance} cicd {config}"
                    f" {' --subscription_id ' + self.subscription_id if self.subscription_id else ''}"
                ),
            ),
        ]
        for (msg, cmd) in commands:
            print(msg)
            subprocess.check_call(cmd, shell=True)

        if self.unattended:
            self.register()

    def register(self) -> None:
        sp_name = "sp_" + self.instance
        print(f"registering {sp_name} to {self.instance}")

        venv = "deploy-venv"
        pip = venv_path(venv, "pip")
        py = venv_path(venv, "python")

        az_cmd = ["az", "account", "show", "--query", "id", "-o", "tsv"]
        subscription_id = subprocess.check_output(az_cmd, encoding="UTF-8")
        subscription_id = subscription_id.strip()

        commands = [
            ("installing prereqs", f"{pip} install -q -r requirements.txt"),
            (
                "running cli registration",
                (
                    f"{py} ./deploylib/registration.py create_cli_registration "
                    f"{self.instance} {subscription_id}"
                    f" --registration_name {sp_name}"
                ),
            ),
        ]

        for (msg, cmd) in commands:
            print(msg)
            output = subprocess.check_output(cmd, shell=True, encoding="UTF-8")
            if "client_id" in output:
                output_list = output.split("\n")
                for line in output_list:
                    if "client_id" in line:
                        line_list = line.split(":")
                        client_id = line_list[1].strip()
                        self.client_id = client_id
                        print(("client_id: " + client_id))
                    if "client_secret" in line:
                        line_list = line.split(":")
                        client_secret = line_list[1].strip()
                        self.client_secret = client_secret
        time.sleep(30)
        return

    def test(
        self,
        test_id: UUID,
        build_id: str,
        check_results: bool,
        is_first_run: bool,
        filename: str,
        region: str,
    ) -> None:
        venv = "test-venv"
        subprocess.check_call(f"python -mvenv {venv}", shell=True)
        py = venv_path(venv, "python")
        test_dir = "integration-test-artifacts"
        script = "integration-test.py"
        endpoint = f"https://{self.instance}.azurewebsites.net"
        test_args = " ".join(self.test_args)
        unattended_args = (
            f"--client_id {self.client_id} --client_secret {self.client_secret}"
            if self.unattended
            else ""
        )
        authority_args = f"--authority {self.authority}" if self.authority else ""
        is_first_run_args = "--is_first_run" if is_first_run else ""
        check_results_args = "--check_results" if check_results else ""

        # targets = "--targets linux-libfuzzer windows-libfuzzer"
        targets = "--targets windows-onefuzz-sample"

        commands = [
            (
                "extracting integration-test-artifacts",
                f"unzip -qq -o {filename} -d {test_dir}",
            ),
            ("test venv", f"python -mvenv {venv}"),
            ("installing wheel", f"./{venv}/bin/pip install -q wheel"),
            ("installing sdk", f"./{venv}/bin/pip install -q {build_id}/sdk/*.whl"),
            (
                "copy sample",
                f"cp -r /mnt/c/Users/statis/Documents/repos/onefuzz/onefuzz/src/integration-tests/windows-onefuzz-sample {test_dir}",
            ),
            (
                "running integration",
                (
                    # f"{py} {test_dir}/{script} test {test_dir} "
                    f"{py} /mnt/c/Users/statis/Documents/repos/onefuzz/onefuzz/src/integration-tests/{script} test {test_dir} "
                    f"--region {region} --endpoint {endpoint} "
                    f"--test_id {test_id} --build_id {build_id} "
                    f"{check_results_args} "
                    f"{is_first_run_args} "
                    f"{targets} "
                    f"{authority_args} "
                    f"{unattended_args} {test_args}"
                ),
            ),
        ]

        for (msg, cmd) in commands:
            print(msg)
            print(cmd)
            subprocess.check_call(cmd, shell=True)

    def cleanup(self, skip: bool) -> None:
        os.chdir(tempfile.gettempdir())
        if skip:
            return

        cmd = ["az", "group", "delete", "-n", self.instance, "--yes", "--no-wait"]
        print(cmd)
        subprocess.call(cmd)

        delete_current_user_app_registrations(self.instance)
        print("done")

    def run(self, *, merge_on_success: bool = False) -> None:
        cwd = os.getcwd()

        test_id = uuid4()
        test_filename = "integration-test-artifacts.zip"
        if not self.skip_tests:
            self.downloader.get_artifact(
                self.repo,
                "ci.yml",
                self.branch,
                self.pr,
                "integration-test-artifacts",
                test_filename,
            )

        if not self.skip_upgrade_test:
            print(
                f"pre-upgrade deploying from {self.repo_upgrade_from}/{self.branch_upgrade_from}"
            )
            os.mkdir("0")
            os.chdir("0")
            release_filename = "release-artifacts-0.zip"
            self.downloader.get_artifact(
                self.repo_upgrade_from,
                "ci.yml",
                self.branch_upgrade_from,
                self.pr_upgrade_from,
                "release-artifacts",
                release_filename,
            )
            self.deploy(release_filename)
            os.chdir(cwd)

        os.mkdir("1")
        os.chdir("1")
        release_filename = "release-artifacts.zip"
        self.downloader.get_artifact(
            self.repo,
            "ci.yml",
            self.branch,
            self.pr,
            "release-artifacts",
            release_filename,
        )
        if self.skip_upgrade_test:
            print(f"deploying from {self.repo}/[branch:{self.branch}][pr:{self.pr}]")
        else:
            if not self.skip_tests:
                print("pre-upgrade starting tests")
                os.chdir(cwd)
                self.test(test_id, "0", False, True, test_filename, self.region)
                os.chdir("1")
            print(f"upgrading from {self.repo}/[branch:{self.branch}][pr:{self.pr}]")
        self.deploy(release_filename)
        os.chdir(cwd)

        if not self.skip_tests:
            if self.skip_upgrade_test:
                print("running tests")
            else:
                print("running post-upgrade tests")
            self.test(
                test_id, "1", True, self.skip_upgrade_test, test_filename, self.region
            )

        if merge_on_success:
            self.merge()


def main() -> None:
    # Get a name that can be added to the resource group name
    # to make it easy to identify the owner
    cmd = ["az", "ad", "signed-in-user", "show", "--query", "mailNickname", "-o", "tsv"]
    name = subprocess.check_output(cmd, encoding="UTF-8")

    # The result from az includes a newline
    # which we strip out.
    name = name.strip()

    default_instance = f"pr-check-{name}-%s" % uuid4().hex
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", default=default_instance)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--branch")
    group.add_argument("--pr", type=int)

    parser.add_argument("--repo", default="microsoft/onefuzz")
    parser.add_argument("--region", default="eastus2")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--skip-cleanup-on-failure", action="store_true")
    parser.add_argument("--merge-on-success", action="store_true")
    parser.add_argument("--subscription_id")
    parser.add_argument("--authority", default=None)
    parser.add_argument("--unattended", action="store_true")

    parser.add_argument("--skip-upgrade-test", action="store_true")
    parser.add_argument("--branch-upgrade-from", default="main")
    parser.add_argument("--pr-upgrade-from", type=int)
    parser.add_argument("--repo-upgrade-from", default="microsoft/onefuzz")
    parser.add_argument("--test-region2", default="southcentralus")

    parser.add_argument("--test_args", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    if not args.branch and not args.pr:
        raise Exception("--branch or --pr is required")

    if (
        not args.branch_upgrade_from
        and not args.pr_upgrade_from
        and not args.skip_upgrade_test
    ):
        raise Exception(
            "--branch_upgrade_from or --pr_updgrade_from is required when doing upgraded test"
        )

    d = Deployer(
        branch=args.branch,
        pr=args.pr,
        instance=args.instance,
        region=args.region,
        subscription_id=args.subscription_id,
        skip_tests=args.skip_tests,
        test_args=args.test_args,
        repo=args.repo,
        unattended=args.unattended,
        authority=args.authority,
        repo_upgrade_from=args.repo_upgrade_from,
        branch_upgrade_from=args.branch_upgrade_from,
        pr_upgrade_from=args.pr_upgrade_from,
        skip_upgrade_test=args.skip_upgrade_test,
    )
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        print(f"running from within {directory}")

        try:
            d.run(merge_on_success=args.merge_on_success)
            input("press any key")
            d.cleanup(args.skip_cleanup)
            return
        finally:
            input("press any key")
            if not args.skip_cleanup_on_failure:
                d.cleanup(args.skip_cleanup)
        os.chdir(tempfile.gettempdir())


if __name__ == "__main__":
    main()
