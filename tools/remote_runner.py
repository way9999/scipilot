"""SSH远程GPU执行工具

通过SSH+SFTP将实验代码上传到GPU服务器、执行训练、下载结果，
自动回填论文写作流程。移植自 PaperForge engine/remote_runner.py。

安装: pip install paramiko pyyaml
配置: 在项目根目录创建 remote.yaml（参考 remote.example.yaml）
"""

from __future__ import annotations

import fnmatch
import os
import stat
import time
from pathlib import Path
from typing import Any


def _lazy_paramiko():
    try:
        import paramiko
        return paramiko
    except ImportError:
        raise ImportError("paramiko 未安装，请运行: pip install paramiko")


def _resolve_env(value: Any) -> str:
    """解析 $ENV_VAR 形式的环境变量引用。"""
    if not isinstance(value, str):
        return str(value) if value is not None else ""
    if value.startswith("$"):
        return os.environ.get(value[1:], "")
    return value


def load_remote_config(config_path: str = "remote.yaml") -> dict:
    """加载远程服务器配置文件。

    配置格式 (remote.yaml):
        host: gpu.example.com
        port: 22
        username: user
        auth:
          method: key          # key 或 password
          key_path: ~/.ssh/id_rsa
          # password: $SSH_PASSWORD  # 支持环境变量
        remote_workdir: /home/user/experiment
        upload_paths:          # 上传的本地路径列表
          - src/
          - configs/
          - train.py
        upload_excludes:       # 排除的文件模式
          - __pycache__
          - "*.pyc"
          - .git
        train_command: "python train.py --config configs/base.yaml"
        results_dir: results/  # 远程结果目录（相对于 remote_workdir）
        poll_interval_seconds: 30
        connect_timeout: 15
    """
    try:
        import yaml
    except ImportError:
        raise ImportError("pyyaml 未安装，请运行: pip install pyyaml")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # 解析密码中的环境变量
    auth = cfg.get("auth", {})
    for key in ("password", "passphrase"):
        if key in auth:
            auth[key] = _resolve_env(auth[key])

    defaults = {
        "port": 22,
        "username": "root",
        "auth": {"method": "key", "key_path": "~/.ssh/id_rsa"},
        "remote_workdir": "/root/experiment",
        "upload_paths": [],
        "upload_excludes": ["__pycache__", ".git", "*.pyc", ".DS_Store"],
        "poll_interval_seconds": 30,
        "connect_timeout": 15,
    }
    for k, v in defaults.items():
        cfg.setdefault(k, v)

    if not cfg.get("host"):
        raise ValueError("remote.yaml: 必须指定 'host'")
    if not cfg.get("train_command"):
        raise ValueError("remote.yaml: 必须指定 'train_command'")
    if not cfg.get("results_dir"):
        raise ValueError("remote.yaml: 必须指定 'results_dir'")

    return cfg


class RemoteRunner:
    """管理SSH连接、文件传输和远程命令执行。

    用法:
        with RemoteRunner(cfg) as runner:
            runner.upload()                    # 上传代码
            exit_code = runner.run_command()   # 执行训练
            runner.download("./results")        # 下载结果

        # 或一步完成:
        with RemoteRunner(cfg) as runner:
            runner.run_full_cycle("./results")
    """

    def __init__(self, config: dict):
        self.cfg = config
        self._paramiko = _lazy_paramiko()
        self._ssh = None
        self._sftp = None

    # ── 连接管理 ────────────────────────────────────────────────────────

    def connect(self):
        """建立SSH连接。"""
        paramiko = self._paramiko
        self._ssh = paramiko.SSHClient()
        self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        auth = self.cfg["auth"]
        connect_kwargs: dict = {
            "hostname": self.cfg["host"],
            "port": self.cfg["port"],
            "username": self.cfg["username"],
            "timeout": self.cfg["connect_timeout"],
        }

        if auth.get("method") == "password":
            connect_kwargs["password"] = auth.get("password", "")
        else:
            key_path = Path(auth.get("key_path", "~/.ssh/id_rsa")).expanduser()
            passphrase = auth.get("passphrase") or None
            connect_kwargs["key_filename"] = str(key_path)
            if passphrase:
                connect_kwargs["passphrase"] = passphrase

        print(f"[remote] 连接到 {self.cfg['username']}@{self.cfg['host']}:{self.cfg['port']} ...")
        self._ssh.connect(**connect_kwargs)
        self._sftp = self._ssh.open_sftp()
        print("[remote] 连接成功")

    def disconnect(self):
        if self._sftp:
            self._sftp.close()
            self._sftp = None
        if self._ssh:
            self._ssh.close()
            self._ssh = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    # ── 文件传输 ────────────────────────────────────────────────────────

    def _should_exclude(self, name: str, excludes: list[str]) -> bool:
        return any(fnmatch.fnmatch(name, pat) for pat in excludes)

    def _sftp_makedirs(self, remote_path: str):
        """递归创建远程目录。"""
        parts = remote_path.replace("\\", "/").split("/")
        current = ""
        for part in parts:
            if not part:
                current = "/"
                continue
            current = f"{current}/{part}" if current != "/" else f"/{part}"
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                self._sftp.mkdir(current)

    def _upload_path(self, local: Path, remote_base: str, excludes: list[str]):
        """递归上传文件或目录。"""
        if self._should_exclude(local.name, excludes):
            return
        remote_path = f"{remote_base}/{local.name}"
        if local.is_dir():
            self._sftp_makedirs(remote_path)
            for child in sorted(local.iterdir()):
                self._upload_path(child, remote_path, excludes)
        else:
            print(f"[remote] 上传 {local} -> {remote_path}")
            self._sftp.put(str(local), remote_path)

    def upload(self):
        """上传配置中指定的本地路径到远程工作目录。"""
        workdir = self.cfg["remote_workdir"]
        excludes = self.cfg["upload_excludes"]
        upload_paths = self.cfg["upload_paths"]

        self._sftp_makedirs(workdir)

        if not upload_paths:
            print("[remote] upload_paths 为空，跳过上传")
            return

        for p in upload_paths:
            local = Path(p).resolve()
            if not local.exists():
                print(f"[remote] 警告: {local} 不存在，跳过")
                continue
            self._upload_path(local, workdir, excludes)

        print("[remote] 上传完成")

    def _download_dir(self, remote_dir: str, local_dir: Path, excludes: list[str]):
        """递归下载远程目录。"""
        local_dir.mkdir(parents=True, exist_ok=True)
        for entry in self._sftp.listdir_attr(remote_dir):
            if self._should_exclude(entry.filename, excludes):
                continue
            remote_path = f"{remote_dir}/{entry.filename}"
            local_path = local_dir / entry.filename
            if stat.S_ISDIR(entry.st_mode):
                self._download_dir(remote_path, local_path, excludes)
            else:
                print(f"[remote] 下载 {remote_path} -> {local_path}")
                self._sftp.get(remote_path, str(local_path))

    def download(self, local_dir: str = "./remote_results"):
        """下载远程结果目录到本地。"""
        remote_results = f"{self.cfg['remote_workdir']}/{self.cfg['results_dir']}"
        excludes = self.cfg.get("download_excludes", ["__pycache__", "*.pyc", ".git"])
        local = Path(local_dir)
        print(f"[remote] 下载 {remote_results} -> {local}")
        self._download_dir(remote_results, local, excludes)
        print("[remote] 下载完成")

    # ── 命令执行 ────────────────────────────────────────────────────────

    def run_command(self, command: str | None = None, stream_output: bool = True) -> int:
        """在远程服务器执行命令，返回退出码。

        Args:
            command: 要执行的命令，默认使用配置中的 train_command
            stream_output: 是否实时打印输出
        """
        cmd = command or self.cfg["train_command"]
        workdir = self.cfg["remote_workdir"]
        full_cmd = f"cd {workdir} && {cmd}"

        print(f"[remote] 执行: {cmd}")
        _, stdout, stderr = self._ssh.exec_command(full_cmd, get_pty=True)

        if stream_output:
            for line in stdout:
                print(f"[remote] {line}", end="")

        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err = stderr.read().decode(errors="replace")
            if err.strip():
                print(f"[remote] stderr: {err}")
        print(f"[remote] 退出码: {exit_code}")
        return exit_code

    # ── 一键完整流程 ────────────────────────────────────────────────────

    def run_full_cycle(self, local_results_dir: str = "./remote_results") -> int:
        """上传 -> 执行训练 -> 下载结果。

        Returns:
            训练命令的退出码
        """
        self.upload()
        exit_code = self.run_command()
        self.download(local_results_dir)
        return exit_code


# ── 便捷函数 ────────────────────────────────────────────────────────────

def run_remote(
    config_path: str = "remote.yaml",
    local_results_dir: str = "./remote_results",
    upload_only: bool = False,
    download_only: bool = False,
) -> int:
    """便捷接口：加载配置并执行完整远程训练流程。

    Args:
        config_path: remote.yaml 路径
        local_results_dir: 下载结果到本地的目录
        upload_only: 只上传，不执行
        download_only: 只下载，不上传不执行

    Returns:
        退出码（0表示成功）
    """
    cfg = load_remote_config(config_path)
    with RemoteRunner(cfg) as runner:
        if upload_only:
            runner.upload()
            return 0
        if download_only:
            runner.download(local_results_dir)
            return 0
        return runner.run_full_cycle(local_results_dir)


# ── CLI ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SSH远程GPU训练工具")
    parser.add_argument("--config", default="remote.yaml", help="remote.yaml 路径")
    parser.add_argument("--download-dir", default="./remote_results", help="结果下载目录")
    parser.add_argument("--upload-only", action="store_true", help="只上传")
    parser.add_argument("--download-only", action="store_true", help="只下载")
    args = parser.parse_args()

    raise SystemExit(
        run_remote(
            config_path=args.config,
            local_results_dir=args.download_dir,
            upload_only=args.upload_only,
            download_only=args.download_only,
        )
    )
